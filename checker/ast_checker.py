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
   - **Unreplaced marker**: The SQL string contains unreplaced template markers like
     `__IN_CLAUSE__`, `__MARKER__`, or similar sentinels. Will cause SQL syntax error.
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
Respond with ONLY a JSON object, no other text:
```json
{
  "failure": false
}
```
OR
```json
{
  "failure": true,
  "category": "not_executable | name_error | db_error | reward_hacking | slow",
  "reason": "specific explanation of what is wrong"
}
```

## Code to analyze
```python
__CODE__
```"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
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
# LLM call
# ---------------------------------------------------------------------------
def _call_llm(code: str, model_name: str) -> dict:
    from google import genai

    client = genai.Client()
    prompt = CHECKER_PROMPT.replace("__CODE__", code[:32000])
    response = client.models.generate_content(model=model_name, contents=prompt)
    text = response.text.strip()

    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        return json.loads(json_match.group(0))
    return {"failure": True, "category": "not_executable", "reason": f"LLM returned unparseable response: {text[:200]}"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def check_code(code: str, model_name: str = "gemini-2.5-flash") -> CheckResult:
    if not code.strip():
        return CheckResult(
            failure=True, reason="Empty code", category="not_executable",
            guardrail_failed=True,
        )

    passed, error = _check_syntax(code)
    if not passed:
        return CheckResult(
            failure=True, reason=error, category="not_executable",
            guardrail_failed=True,
        )

    result = _call_llm(code, model_name)
    return CheckResult(
        failure=result.get("failure", False),
        reason=result.get("reason", ""),
        category=result.get("category", ""),
    )


def check_file(path: str, model_name: str = "gemini-2.5-flash") -> CheckResult:
    with open(path) as f:
        return check_code(f.read(), model_name)


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

    result = check_file(path, model_name=args.model)

    if args.json:
        print(format_results_json(result))
    else:
        print(format_results(result))

    sys.exit(0 if not result.failure else 1)


if __name__ == "__main__":
    main()
