from __future__ import annotations

import json

from .intent import IntentSpec, TransactionIntent, QueryIntent


def _as_int(value) -> int:
    """Safely coerce a value to int; return 0 on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _build_intent_prompt(
    tree: str,
    runner_content: str,
    file_contents: dict[str, str],
    runner_path: str,
    target_path: str,
) -> str:
    support_paths = [p for p in file_contents if p != runner_path and p != target_path]

    parts = [
        "You are analyzing a codebase for an application-database co-optimization pipeline. "
        "Your job is to produce a precise, machine-readable intent specification that another LLM "
        "will use to rewrite the baseline code. Be exact and complete.",
        "",
        "## RUNNER FILE (entry point — study this carefully)",
        f"The runner at `{runner_path}` is the main entry point. Study it to determine: "
        "how the system is invoked (run command), how it discovers and loads implementations "
        "(file naming convention, class naming convention, import mechanism, directory placement). "
        "These loading conventions are critical — the generated code must follow them exactly.",
        "",
        "Pay special attention to:",
        "- **Argument parsing**: Find `argparse` or `sys.argv` usage. Identify what CLI arguments "
        "the runner expects (e.g. driver name, config path, duration). The generated driver may "
        "need to match an expected argument interface.",
        "- **File-to-class mapping**: Look for patterns like `getDrivers()` that glob `*driver.py` "
        "files and strip the `driver.py` suffix to derive a driver name `<name>`. The runner then "
        "computes the class as `<Name>Driver` where `<Name>` = `<name>.title()`. The file is "
        "`<name>driver.py` and the module is `tpcc.drivers.<name>driver`. "
        "Example: file `optimizedmysqldriver.py` -> name `optimizedmysql` -> class `OptimizedmysqlDriver` "
        "(NOT `OptimizedmysqldriverDriver` — the filename already contains 'driver', the runner strips it "
        "before title-casing and appending 'Driver').",
        "- **Import mechanism**: How does the runner load implementations? "
        "Does it use `__import__()`, `importlib`, or a dynamic import pattern? "
        "The output class must be discoverable by this mechanism.",
        "- **Execution flow**: How does the runner invoke transaction methods? "
        "What method signature does it expect? What object does it pass as `params`?",
        f"```python",
        runner_content,
        "```",
        "",
        "## SUPPORT FILES (context for the baseline code)",
        "These files provide context. For each, produce a summary and its relationship to the baseline.",
        "",
        "## BASELINE TARGET FILE",
        f"This is the file to optimize: `{target_path}`. "
        "Analyze its database interactions in detail.",
        "",
        "### What to extract for each transaction method",
        "For every method that performs database work (e.g. `doDelivery`, `doNewOrder`), identify:",
        "1. **Method signature** — exact Python def line and what `params` dictionary keys it reads.",
        "2. **Return shape** — what the method returns (tuple/list/dict/integer) and what each element means.",
        "3. **Queries in execution order** — every SQL statement executed, in order. For each query record:",
        "   - `sql_template`: the SQL string exactly as it appears (preserve `%s`, `%d`, `%02d`, `%%s` placeholders).",
        "   - `purpose`: what information the query reads or writes.",
        "   - `params`: list of Python variable names that feed the placeholders in order (e.g. `['w_id', 'd_id']`).",
        "   - `in_loop`: boolean — is this query inside a `for`/`while` loop over input data?",
        "   - `loop_variable`: if `in_loop`, the loop variable name (e.g. `i`).",
        "   - `result_uses`: how the result is consumed (e.g. `fetchone()[0]`, `fetchall()`).",
        "4. **Dataflow between queries** — which result columns from query N are used as parameters in query N+1.",
        "5. **Loop-batchable work** — identify groups of per-iteration queries that can be replaced by a single set-based query (e.g. `IN (...)` or `executemany`).",
        "6. **Merge opportunities** — identify sequential SELECTs that can be combined into one JOIN, or sequential UPDATEs/INSERTs that can be batched.",
        "7. **Side effects and ordering constraints** — flag any UPDATE/INSERT/DELETE whose order matters for correctness (e.g. incrementing a counter before inserting a child row).",
        "",
        "### What to extract for the optimized file",
        "- `output_target`: the full path where the optimized file should be written. It MUST follow the runner's "
        "file/class conventions so the runner can discover it. For TPC-C style runners, the file must be in the same "
        "directory as other `*driver.py` files and its class name must match the runner's expectation. "
        "Example: if the target is `tpcc/drivers/mysqldriver.py` and you are generating an optimized variant, "
        "a valid output_target would be `tpcc/drivers/optimizedmysqldriver.py` (class `OptimizedmysqlDriver`).",
    ]

    for path, content in file_contents.items():
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        lang = {"py": "python", "js": "javascript", "ts": "typescript", "sql": "sql", "java": "java"}.get(ext, ext)
        label = "RUNNER" if path == runner_path else "SUPPORT" if path in support_paths else "TARGET"
        parts.append(f"### [{label}] {path}")
        parts.append(f"```{lang}")
        parts.append(content)
        parts.append("```")
        parts.append("")

    schema = """{
  "db_type": "string — e.g. mysql, postgresql",
  "db_api": "string — e.g. MySQLdb, psycopg2",
  "db_version": "string — database version, infer from the project tree (e.g. docker-compose.yml) and SQL features. Example: 'MySQL 5.7'. If uncertain, use 'unknown'",
  "summary": "string — project purpose and database usage",
  "target_file": "string — file path of the baseline implementation to optimize",
  "output_target": "string — full file path where the optimized file should be written. MUST follow runner discovery conventions",
  "plan_summary": "string — concrete optimization plan: which queries to merge, batch, or rewrite and why",
  "support_summary": "string — how the system runs, discovers implementations, naming/file requirements, and what each support file contributes",
  "runner_summary": "string — detailed analysis of the runner: CLI args, class-name derivation, import mechanism, and constraints the generated file must satisfy",
  "support_summaries": [
    {
      "filename": "string — path of the support file",
      "summary": "string — what this file contains",
      "relationship": "string — how this file relates to the baseline"
    }
  ],
  "conventions": {
    "class_naming": "string — naming convention observed for classes",
    "file_naming": "string — naming convention observed for files",
    "loading_mechanism": "string — how implementations are discovered/loaded"
  },
  "transactions": [
    {
      "name": "string — transaction/method name, e.g. doNewOrder",
      "description": "string — what the transaction does",
      "method_signature": "string — exact Python def line",
      "return_shape": "string — description of return value",
      "file_path": "string",
      "line_number": "integer",
      "dataflow_summary": "string — how data flows between queries and Python variables",
      "queries": [
        {
          "sql_template": "string — exact SQL template with placeholders",
          "purpose": "string — what this query reads or writes",
          "params": ["string — Python variable names that fill placeholders, in order"],
          "in_loop": "boolean",
          "loop_variable": "string or null",
          "result_uses": "string — e.g. fetchone()[0], fetchall()"
        }
      ],
      "optimization_notes": "string — loop batches, merge opportunities, ordering constraints"
    }
  ]
}"""

    parts.extend([
        "Return a JSON object matching this schema exactly:",
        schema,
        "",
        "Respond with ONLY the JSON object, no explanation. "
        'If you cannot determine a field, use "unknown" or null as appropriate. '
        "Ensure all string values are valid JSON strings (escape quotes and newlines).",
    ])

    return "\n".join(parts)


def _parse_intent_response(text: str) -> IntentSpec | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        brace = text.find("{")
        if brace >= 0:
            try:
                data = json.loads(text[brace:])
            except json.JSONDecodeError:
                return None
        else:
            return None

    transactions = []
    for t in data.get("transactions", []):
        queries = []
        for q in t.get("queries", []):
            queries.append(QueryIntent(
                name=q.get("name", ""),
                sql_template=q.get("sql_template", ""),
                params=q.get("params", []) if isinstance(q.get("params"), list) else [],
                purpose=q.get("purpose", ""),
                file_path=t.get("file_path", ""),
                line_number=_as_int(t.get("line_number", 0)),
            ))
        transactions.append(TransactionIntent(
            name=t.get("name", "unknown"),
            description=t.get("description", ""),
            queries=queries,
            dataflow_summary=t.get("dataflow_summary", ""),
            method_signature=t.get("method_signature", ""),
            file_path=t.get("file_path", ""),
            line_number=_as_int(t.get("line_number", 0)),
        ))

    # Normalize output_target to a sensible default if missing or malformed.
    output_target = data.get("output_target", "")
    if not output_target or output_target in ("unknown", "null"):
        output_target = ""

    return IntentSpec(
        summary=data.get("summary", ""),
        db_type=data.get("db_type", "unknown"),
        db_api=data.get("db_api", "unknown"),
        db_version=data.get("db_version", ""),
        target_file=data.get("target_file", ""),
        output_target=output_target,
        plan_summary=data.get("plan_summary", ""),
        support_summary=data.get("support_summary", ""),
        support_summaries=data.get("support_summaries", []),
        runner_summary=data.get("runner_summary", ""),
        transactions=transactions,
        conventions=data.get("conventions", {}),
    )


def extract_intent(
    tree: str,
    runner_content: str,
    file_contents: dict[str, str],
    client: "genai.Client",
    model_name: str,
    runner_path: str = "",
    target_path: str = "",
    dry_run: bool = False,
) -> IntentSpec:
    prompt = _build_intent_prompt(tree, runner_content, file_contents, runner_path, target_path)

    if dry_run:
        print("=== INTENT EXTRACTION PROMPT ===")
        print(prompt)
        print("=== END INTENT PROMPT ===")
        return IntentSpec(summary="(dry-run)", db_type="unknown", db_api="unknown", db_version="", support_summary="(dry-run)", runner_summary="(dry-run)")

    response = client.models.generate_content(model=model_name, contents=prompt)
    result = response.text.strip()
    intent = _parse_intent_response(result)

    if intent:
        return intent

    return IntentSpec(
        summary=f"Intent extraction produced unparseable response. Raw analysis:\n{result[:1000]}",
        db_type="unknown",
        db_api="unknown",
        db_version="",
        runner_summary="",
    )
