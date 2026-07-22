from __future__ import annotations

import os

from .intent import IntentSpec
from .planner import StrategyDef


def build_optimization_prompt(
    tree: str,
    runner_content: str,
    target_content: str,
    support_contents: dict[str, str],
    intent: IntentSpec,
    strategies: list[StrategyDef],
    output_path: str,
) -> str:
    basename = os.path.basename(output_path)

    strategies_text = "\n\n".join(
        f"## {s.name}\n*Objective*: {s.objective}\n*Mechanisms*: {s.mechanisms}\n"
        + (f"*Example*:\n```python\n{s.example_snippet}\n```" if s.example_snippet else "")
        for s in strategies
    )

    parts = [
        "You are an application-database co-optimization engine. "
        "Analyze the intent and source files below, then generate an optimized version "
        "that reduces database round-trips, improves query efficiency, and preserves correctness.",
        "",
        "## PROJECT STRUCTURE",
        tree,
        "",
        "## EXTRACTED INTENT",
        f"Purpose: {intent.summary}",
        f"Database: {intent.db_type} ({intent.db_api})",
        f"Database version: {intent.db_version or 'unknown'}",
        f"Target file: {intent.target_file}",
        f"Output file: {output_path}",
        f"Plan: {intent.plan_summary}",
        "",
        f"Transactions ({len(intent.transactions)}):",
    ]

    for txn in intent.transactions:
        parts.append(f"  - {txn.name}: {txn.description} ({txn.round_trips} round-trips)")
        if txn.method_signature:
            parts.append(f"    Signature: {txn.method_signature}")
        if txn.dataflow_summary:
            parts.append(f"    Dataflow: {txn.dataflow_summary}")
        for q in txn.queries:
            loop_note = " [IN LOOP]" if getattr(q, "in_loop", False) else ""
            parts.append(f"    - {q.purpose}: {q.sql_template[:120]}{loop_note}")
    parts.append("")

    if intent.conventions:
        parts.append("## CONVENTIONS")
        for key, val in intent.conventions.items():
            if val:
                label = key.replace("_", " ").title()
                parts.append(f"- {label}: {val}")
        parts.append("")

    if intent.runner_summary:
        parts.append("## RUNNER ANALYSIS")
        parts.append(intent.runner_summary)
        parts.append("")

    if intent.support_summaries:
        parts.append("## SUPPORT FILES (summaries)")
        for s in intent.support_summaries:
            fn = s.get("filename", "?")
            summary = s.get("summary", "")
            rel = s.get("relationship", "")
            parts.append(f"### {fn}")
            if summary:
                parts.append(f"Summary: {summary}")
            if rel:
                parts.append(f"Relationship to baseline: {rel}")
            parts.append("")

    if strategies_text.strip():
        parts.extend([
            "## REWRITE STRATEGIES — Study and Apply",
            strategies_text,
            "",
        ])

    parts.extend([
        "## OUTPUT FILE",
        f"The output will be written to `{basename}`. The class name MUST match the runner's expectation for this filename.",
        "",
        "## RULES",
        "- Output a complete, self-contained, syntactically valid Python file.",
        "- Preserve the baseline file's imports and add any imports needed for helpers (e.g. `itertools`, `re`).",
        "- Preserve the exact class inheritance (`class XxxDriver(AbstractDriver)`) and constructor signature.",
        "- Preserve every public transaction method name from the baseline (e.g. `doDelivery`, `doNewOrder`, `doOrderStatus`, `doPayment`, `doStockLevel`). Do not rename them.",
        "- Keep the module-level `TXN_QUERIES` dict. You may add, rename, or merge its inner query keys to support your optimizations, but the top-level transaction keys must remain.",
        "- Use helper methods (e.g. `_batch_items`, `_batch_stock_info`, `_batch_update_stock`, `_batch_insert_order_lines`, `_batch_delete_new_orders`, `_batch_update_orders`, `_batch_update_order_lines`, `_batch_update_customers`) for loop batching. Put the batched SQL templates in `TXN_QUERIES`, not inline.",
        "- End every transaction method with `self.conn.commit()` before returning.",
        "- Do not change transaction semantics: return the same shape/value as the baseline, preserve all conditional branches, and keep assertions that guard correctness.",
        f"- Target database version: {intent.db_version or 'unknown'}. "
        "Do NOT use features unsupported by this version (e.g., MySQL 5.7 does not support window "
        "functions like `ROW_NUMBER()`, `RANK()`, `OVER`, `PARTITION BY`, `LATERAL`, or CTEs). "
        "Use standard SQL (GROUP BY, subqueries, joins) compatible with the target version.",
        "- **EXACT PARAM KEYS ONLY**. Variables extracted from `params[...]` must use the exact key as-is. "
        "`params[\"w_id\"]` → `w_id`, `params[\"c_w_id\"]` → `c_w_id`, `params[\"c_id\"]` → `c_id`. "
        "You MUST NOT invent composite names like `w_w_id`, `d_d_id`, `c_c_id`, `w_w_id_d`, etc. These are undefined variables and will cause the generated code to fail. "
        "If a query needs the warehouse ID of the current transaction, use `w_id`. If it needs the customer's warehouse ID, use `c_w_id` exactly as it appears in `params`. "
        "Never add a prefix to a param key: the key in `params` is the final variable name.",
        "- Every `cursor.execute(sql, params)` call must have exactly as many parameters as `%s` placeholders in the final SQL string. Build IN-clause placeholders dynamically to match the number of values.",
        "- Use the `__IN_CLAUSE__` marker for dynamic IN clauses that must be inserted into a SQL template before `cursor.execute`. Do NOT use bare `%s` for an IN clause that will be filled with a generated string of placeholders; that causes 'not enough arguments for format string' errors. "
        "Examples:",
        "  - Simple batch SELECT (no other scalar param): `q[\"getItemInfo_batch\"] % in_clause_i_ids` where the template is `SELECT ... FROM ITEM WHERE I_ID IN %s` and `in_clause_i_ids` is a string like `(\"%s\",\"%s\")`. The `%s` placeholders inside the IN clause are bound by `cursor.execute`.",
        "  - Batch query with a scalar + IN clause: template is `UPDATE ORDERS SET O_CARRIER_ID = %s WHERE (O_ID, O_D_ID, O_W_ID) IN __IN_CLAUSE__`. Code: `query = q[\"updateOrders_batch\"].replace(\"__IN_CLAUSE__\", in_clause_str); self.cursor.execute(query, [o_carrier_id] + flattened_params)`. This keeps all values parameterized.",
        "  - Dynamic column name + IN clause: for TPC-C NEW_ORDER batch stock lookup, include `S_W_ID` in the SELECT list so each row can be uniquely identified: template is `SELECT S_I_ID, S_W_ID, S_QUANTITY, S_DATA, S_YTD, S_ORDER_CNT, S_REMOTE_CNT, S_DIST_%02d FROM STOCK WHERE (S_I_ID, S_W_ID) IN __IN_CLAUSE__`. Code: `query = q[\"getStockInfo_batch_template\"] % d_id; query = query.replace(\"__IN_CLAUSE__\", in_clause_stock_pairs); self.cursor.execute(query, flattened_stock_params)`. Then build the dictionary as `stock_rows = {(row[0], row[1]): row for row in self.cursor.fetchall()}` — key is `(S_I_ID, S_W_ID)`. The `S_DIST_%02d` value is at the last column index (7), not index 6.",
        "- You may use Python `%` formatting ONLY for non-parameter parts of the SQL (e.g. dynamic column names like `S_DIST_%02d`). All actual query values must be passed as parameters to `cursor.execute` to avoid SQL syntax errors and injection risks.",
        "- SQL templates stored in `TXN_QUERIES` use bare `%s` for `cursor.execute` parameter binding. "
        "If a template needs a literal `%` character (e.g. for a pattern match), escape it as `%%` in the source string.",
        "- Do not invent column names or table aliases. Use the exact column and table names from the baseline queries.",
        "",
        "## PRE-SUBMISSION SELF-CHECK",
        "Before outputting the code, verify:",
        "1. The class name matches the runner's expectation for the output filename.",
        "2. Every transaction method from the baseline is present and has the same signature.",
        "3. There are NO variables named `w_w_id`, `d_d_id`, `c_c_id`, or similar prefixed param keys.",
        "4. Every `cursor.execute(sql, params)` has the same number of `%s` placeholders as list/tuple elements in `params` after `__IN_CLAUSE__` markers are replaced.",
        "5. For TPC-C NEW_ORDER, the batch stock SELECT returns both `S_I_ID` and `S_W_ID`, and the resulting dictionary is keyed by `(S_I_ID, S_W_ID)` (not by `S_DIST_XX`).",
        "6. The file ends with every transaction method calling `self.conn.commit()`.",
        "7. The file is valid Python (no syntax errors, balanced parentheses/brackets).",
        "",
        "## OPTIMIZATION RECIPE",
        "1. Merge sequential SELECTs into JOINs when they share the same warehouse/district/customer keys.",
        "2. Replace per-item loops with set-based `IN (...)` batch SELECTs and `executemany` batch writes.",
        "3. Use derived tables / subqueries to push filters early (predicate pushdown) when joining large tables.",
        "4. Use `STRAIGHT_JOIN` only when you need to force a specific join order; otherwise rely on standard joins.",
        "5. Keep write ordering constraints: updates that depend on prior reads must still read first.",
        "",
        "## RUNNER FILE (entry point — must follow these conventions)",
        "Study the runner's `getDrivers()` and `createDriverClass()` to understand the naming convention.",
        "The class name must be `<Name>Driver` where `<Name>` is the title-cased version of the driver name "
        "(the filename with `driver.py` stripped). "
        "Example: file `optimizedmysqldriver.py` -> class `OptimizedmysqlDriver` (NOT `OptimizedmysqldriverDriver`).",
        "```python",
        runner_content,
        "```",
        "",
    ])

    for path, content in support_contents.items():
        parts.extend([
            f"## SUPPORT FILE: {os.path.basename(path)}",
            "```python",
            content,
            "```",
            "",
        ])

    parts.extend([
        "## BASELINE TARGET FILE (code to optimize)",
        "```python",
        target_content,
        "```",
        "",
        "Generate the COMPLETE optimized code now. Output ONLY code, no explanation.",
    ])

    return "\n".join(parts)


def generate_optimizations(
    prompt: str,
    output_path: str,
    model_name: str,
    client: "genai.Client",
    dry_run: bool = False,
) -> str:
    if dry_run:
        return ""

    response = client.models.generate_content(model=model_name, contents=prompt)
    result = response.text.strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[1]
        result = result.rsplit("```", 1)[0]
        result = result.strip()

    with open(output_path, "w") as f:
        f.write(result)
        if not result.endswith("\n"):
            f.write("\n")

    return result
