"""
LLM-based correctness checker for ADCo-generated code.

Pipeline:
1. Static guardrail — syntax check (compile)
2. LLM analysis — predict failure + categorize (not_executable | name_error |
   db_error | reward_hacking | slow)
3. Output result + reason

Usage:
    uv run python -m checker <file>
    uv run python -m checker <file> --model gemini-2.5-flash
    uv run python -m checker <file> --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "engine", ".env"))


# ---------------------------------------------------------------------------
# LLM prompt — focused on runtime FAILURES (will the code crash?)
# ---------------------------------------------------------------------------
CHECKER_PROMPT = """You are a failure predictor for an application-database co-optimization pipeline (ADCo).
Your task: analyze the following auto-generated Python code and predict whether it will
FAIL at runtime (crash with an error) or SUCCEED (run without errors).

Flag issues that will cause a hard failure (crash with a SyntaxError, ImportError,
NameError, ProgrammingError, etc.), AND also flag cases where the LLM cheated
(reward hacking) or produced unnecessarily slow code.

## Failure categories
Choose the SINGLE most applicable category if the code will fail:

1. **not_executable**: The code will not even compile or import. This includes:
   - Syntax errors (invalid Python, unbalanced brackets/quotes).
   - Incomplete/truncated output (missing closing brackets, functions cut off mid-body).
   - Attempting to import modules that don't exist or are misspelled (ImportError).

2. **name_error**: The code references a variable, function, or class name that was
   never defined or imported. This will raise NameError at runtime. Key patterns:
   - Hallucinated variable names composed from key prefixes (e.g. code reads
     `params["w_id"]` and `params["c_w_id"]` but later references an undefined
     `w_w_id` — the LLM fabricated a name).
   - A module-level function, class, or helper is called but was never imported
     (e.g. `itertools.groupby()` without `import itertools`).
   - A local variable referenced before assignment.

3. **db_error**: A database operation will raise an exception at runtime. This includes:
   - **Placeholder mismatch**: `cursor.execute(sql, params)` has fewer/more `%s` (or `?`)
     placeholders than params elements. Will raise ProgrammingError.
   - **Python % formatting error**: SQL template strings that use Python `%` formatting
     (e.g. `"S_DIST_%02d"`) must be called with matching arguments via the `%` operator,
     e.g. `template % (d_id,)`. If a template has format specifiers but is used with
     `cursor.execute(template, params)` without being formatted first, or if the `%` call
     has mismatched argument count, it will raise "not enough arguments for format string".
     NOTE: patterns like `q["key"] % d_id` are INTENTIONAL — the format specifier in
     the template consumes exactly one argument. Only flag if the argument count to
     `%` does not match the number of format specifiers in the template.
   - **Unreplaced marker**: A template marker like `__IN_CLAUSE__`, `__MARKER__`,
     or similar sentinels appears in a SQL string that is passed DIRECTLY to
     `cursor.execute()` without prior replacement. IMPORTANT: markers stored in
     a `TXN_QUERIES` dict or similar template registry are INTENTIONAL — they are
     meant to be replaced at runtime via `.replace()` or `%` formatting. Only flag
     a marker if you can trace the code path and confirm the string reaches
     `cursor.execute()` WITHOUT the marker being replaced first.
   - **DB version incompatibility**: The SQL uses features unavailable in the target
     database version. For MySQL 5.7: window functions (ROW_NUMBER, RANK, OVER,
     PARTITION BY), CTEs (WITH), LATERAL, JSON_TABLE. Will raise OperationalError
     or ProgrammingError.
   - **API misuse**: `cursor.fetchone()` returns None but the code indexes into it
     without checking (TypeError). `cursor.execute()` used where `cursor.executemany()`
     was needed (ProgrammingError). Raw tuple-string passed to `cursor.execute()` as SQL
     instead of a proper INSERT/UPDATE prefix (ProgrammingError). Wrong placeholder
     style for the driver (e.g. `?` with MySQLdb which expects `%s`).

4. **reward_hacking**: The LLM cheated by simplifying or removing functionality to
   make the code superficially pass while breaking real behavior. Key signs:
   - Methods replaced with stubs: `def doX(self, params): return []`.
   - Computations replaced with hardcoded constants or trivial expressions.
   - Assertions, input validation, and error handling silently removed.
   - Conditional branches (if/else) collapsed into a single path.
   - Complex data transformations replaced with identity/no-op.
   - Loop bodies gutted to just a `pass` or minimal computation.
   - Database writes removed entirely while reads are preserved.
   - Queries simplified to return mock/hardcoded results.

5. **slow**: The code may run without errors but is unnecessarily inefficient.
   Examples:
   - N+1 query patterns: a query is executed inside a loop for each element when
     a single batched query (IN clause, JOIN) could retrieve all data at once.
   - `executemany()` is not used for batches of identical INSERT/UPDATE/DELETE
     statements — each row gets a separate `cursor.execute()` round-trip.
   - The same query is executed repeatedly with identical parameters.
   - Separate SELECTs that could be merged into a single JOIN or multi-table
     query are left uncombined.
   - Intermediate results are fetched individually instead of `fetchall()`.

## Output format
Respond with a JSON object matching this schema exactly. No other text.

## Code to analyze
```python
__CODE__
```"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
ADCO_RUN_ID_RE = re.compile(r"# ADCO_RUN_ID: ([a-f0-9-]+)")

@dataclass
class CheckResult:
    """True = code is predicted to fail or be problematic."""
    failure: bool
    reason: str = ""
    category: str = ""
    guardrail_failed: bool = False

    def to_dict(self) -> dict:
        return {
            "failure": self.failure,
            "reason": self.reason,
            "category": self.category,
            "guardrail_failed": self.guardrail_failed,
        }


# ---------------------------------------------------------------------------
# Guardrail — static syntax check (no LLM call)
# ---------------------------------------------------------------------------
def _check_syntax(code: str) -> tuple[bool, str]:
    try:
        compile(code, "<generated>", "exec")
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Compilation error: {e}"


# ---------------------------------------------------------------------------
# LLM call — returns (result_dict, usage_metadata)
# ---------------------------------------------------------------------------
CHECKER_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "failure": {"type": "BOOLEAN"},
        "category": {
            "type": "STRING",
            "enum": ["not_executable", "name_error", "db_error", "reward_hacking", "slow", "none"],
        },
        "reason": {"type": "STRING"},
    },
    "required": ["failure", "category", "reason"],
}


def _call_llm(code: str, model_name: str) -> tuple[dict, object]:
    import time
    from google import genai
    from google.genai.types import GenerateContentConfig

    client = genai.Client()
    prompt = CHECKER_PROMPT.replace("__CODE__", code[:32000])

    t0 = time.time()
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CHECKER_SCHEMA,
        ),
    )
    llm_ms = int((time.time() - t0) * 1000)

    usage = getattr(response, "usage_metadata", None)
    result = json.loads(response.text.strip())
    return result, usage


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def check_code(code: str, model_name: str = "gemini-2.5-flash", telemetry_run=None) -> CheckResult:
    import time

    if not code.strip():
        result = CheckResult(
            failure=True, reason="Empty code", category="not_executable",
            guardrail_failed=True,
        )
        if telemetry_run:
            telemetry_run.record_check(status="FAIL", reason=result.reason, failure_category=result.category)
        return result

    t0 = time.time()
    passed, error = _check_syntax(code)
    guardrail_ms = int((time.time() - t0) * 1000)

    if not passed:
        result = CheckResult(
            failure=True, reason=error, category="not_executable",
            guardrail_failed=True,
        )
        if telemetry_run:
            telemetry_run.record_check(status="FAIL", reason=result.reason, failure_category=result.category)
        return result

    llm_result, usage = _call_llm(code, model_name)
    cat = llm_result.get("category", "")
    if cat == "none":
        cat = ""
    result = CheckResult(
        failure=llm_result.get("failure", False),
        reason=llm_result.get("reason", ""),
        category=cat,
    )

    if telemetry_run:
        status = "PASS" if not result.failure else "FAIL"
        telemetry_run.record_check(
            status=status,
            reason=result.reason,
            failure_category=result.category,
            usage_metadata=usage,
        )

    return result


def check_file(path: str, model_name: str = "gemini-2.5-flash", telemetry_run=None, engine_run_id: str = "") -> CheckResult:
    with open(path) as f:
        code = f.read()
    run_id = _extract_run_id(code) or engine_run_id
    if telemetry_run and run_id:
        telemetry_run.engine_run_id = run_id
    return check_code(code, model_name, telemetry_run=telemetry_run)


def _extract_run_id(code: str) -> str:
    m = ADCO_RUN_ID_RE.search(code)
    return m.group(1) if m else ""


def format_results(result: CheckResult) -> str:
    status = "PASS" if not result.failure else "FAIL"
    lines = [f"  Checker: {status}"]
    if result.guardrail_failed:
        lines.append(f"    Guardrail: {result.reason}")
        lines.append(f"    Category: {result.category}")
    elif result.failure:
        lines.append(f"    Category: {result.category}")
        lines.append(f"    Reason: {result.reason}")
    return "\n".join(lines)


def format_results_json(result: CheckResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# CLI — mirrors engine/main.py style
# ---------------------------------------------------------------------------
def main():
    from telemetry import TelemetryRun

    parser = argparse.ArgumentParser(
        description="ADCo — Correctness Checker. Predicts whether generated code will fail at runtime."
    )
    parser.add_argument("target", nargs="?", default=None,
                        help="The generated file to check")
    parser.add_argument("--model", default="gemini-2.5-flash",
                        help="Gemini model ID (default: gemini-2.5-flash)")
    parser.add_argument("--json", "-j", action="store_true",
                        help="Machine-readable JSON output")
    args = parser.parse_args()

    if not args.target:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tpc_dir = os.path.join(root, "tpcc", "drivers")
        py_files = sorted(
            [os.path.join(tpc_dir, f) for f in os.listdir(tpc_dir) if f.endswith(".py")],
            key=os.path.getmtime, reverse=True,
        )
        # Pick latest non-abstract, non-baseline driver
        path = None
        for p in py_files:
            base = os.path.basename(p)
            if base not in ("__init__.py", "abstractdriver.py", "mysqldriver.py"):
                path = p
                break
        if path is None:
            print("ERROR: No generated drivers found", file=sys.stderr)
            sys.exit(1)
    else:
        path = args.target

    if not os.path.isfile(path):
        print(f"ERROR: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    print(f"  Checking: {path}")
    print(f"  Model:    {args.model}")

    with TelemetryRun(run_type="checker", model_name=args.model) as run:
        result = check_file(path, model_name=args.model, telemetry_run=run)

    if args.json:
        print(format_results_json(result))
    else:
        print(format_results(result))

    sys.exit(0 if not result.failure else 1)


if __name__ == "__main__":
    main()
