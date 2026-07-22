We're building **ADCo (Application-Database Co-design)** — a research framework that jointly analyzes application code and database interactions to find optimization opportunities invisible to either layer in isolation.

The framework should scan any codebase, detect database interactions (SQL queries, cursor.execute calls, ORM usage), extract the semantic intent of each transaction, match the extracted patterns against a knowledge base of rewrite strategies (query combining, predicate pushdown, join ordering, query separation, and batching for concurrency), then generate an optimized version of the code that reduces round-trips and improves query efficiency while preserving correctness.

Keep the scope focused on a two-LLM-call pipeline: the first LLM call extracts structured intent from the scanned codebase, and the second generates the optimized code guided by that intent plus the knowledge base. Use Google's Gemini API (defaulting to gemini-2.5-flash) for inference. The main target is MySQL 5.7 — generated code must avoid window functions, CTEs, and other unsupported features. Verification includes Python syntax checking, MySQL 5.7 compatibility checks, and guards against common LLM hallucinations (e.g., invented variable names like `w_w_id`).

The project extends [`apavlo/py-tpcc`](https://github.com/apavlo/py-tpcc) — a Python TPC-C benchmark implementation — and uses it as a built-in test case. When the scanner detects TPC-C patterns (WAREHOUSE/DISTRICT/CUSTOMER tables, doNewOrder/doDelivery methods, TXN_QUERIES dict), the engine auto-injects TPC-C-specific rules and validation. The generated drivers follow a strict naming convention: `tpcc/drivers/<name>driver.py` with a class `<Name>Driver` that extends `AbstractDriver`. The benchmark runner discovers drivers by globbing `*driver.py`, stripping the suffix, title-casing, and appending `Driver`.

For architecture, the pipeline has four stages: a Scanner that walks the codebase and builds a project tree, an Intent Extractor (LLM call #1) that produces a structured IntentSpec with transactions and queries, a Planner that parses the knowledge base into strategy definitions, and a Generator (LLM call #2) that builds a prompt from the scan, intent, plan, and knowledge base to produce optimized code. A Verifier then checks the output for syntax errors, MySQL 5.7 compatibility, and known hallucination patterns.

For data flow, the runner file and supporting files are read alongside the target file. All file contents are sent to the intent extractor LLM, which returns a JSON IntentSpec. That spec is combined with the parsed knowledge base strategies to form the generation prompt. The generated code is written to an output file, verified, and then can be benchmarked via the TPC-C runner.

For routes, the entry point is `engine/main.py` invoked via `python -m engine.main <target> --runner <runner> [--with <support> ...]`. The Makefile provides shortcuts: `make gen` for TPC-C generation, `make run <driver>` for benchmarking, `make test-unit` for AST-based static checks, and `make gen-generic` for arbitrary codebases.

For the data model, the core intent structure is: an `IntentSpec` containing a summary, db_type, db_api, db_version, output_target, conventions, and a list of `TransactionIntent` objects, each of which has a name, description, method_signature, dataflow_summary, and a list of `QueryIntent` objects (sql_template, params, purpose, in_loop, result_uses). The Planner produces `StrategyDef` objects (name, objective, mechanisms, example_snippet). This can be visualized as a graph: Codebase → Scanner → CodebaseProfile → IntentExtractor → IntentSpec → Planner → [StrategyDef] → Generator → Optimized Code → Verifier → Result.

For build phases, first comes the scanner and intent data structures (already done), then the intent extractor prompt engineering and JSON parsing (done), then the planner and knowledge base format (done), then the generator prompt with rewrite strategies (done), then the verifier with compile+compatibility checks (done), then the TPC-C integration and AST-based testing (done), and finally the generic mode for arbitrary codebases.

Key risks and edge cases: LLM hallucinates variable names by prefixing param keys (`w_w_id` instead of `w_id`), generates window functions incompatible with MySQL 5.7, produces SQL with mismatched `%s` placeholders and parameters, omits required helper methods, or fails to end transaction methods with `self.conn.commit()`. The scanner may miss DB interactions in non-Python files. The intent extractor may return unparseable JSON. The generic pipeline (non-TPC-C) has less validation.

Copyable starter prompt for a coding agent:

```
You are working on ADCo, an application-database co-optimization framework.
The pipeline: Scanner → Intent Extractor (LLM) → Planner → Generator (LLM) → Verifier.
The scanner builds a project tree and lists source files.
The intent extractor sends code to Gemini and parses JSON into IntentSpec.
The planner parses docs/kb/query_rewrite_methods.md into StrategyDef objects.
The generator builds a prompt combining scan + intent + strategies and calls Gemini.
The verifier checks syntax, MySQL 5.7 compatibility, and hallucination patterns.

Key constraints:
- TPC-C drivers: file tpcc/drivers/<name>driver.py → class <Name>Driver(AbstractDriver)
- 5 required methods: doDelivery, doNewOrder, doOrderStatus, doPayment, doStockLevel
- 8 required helpers: _batch_items, _batch_stock_info, _batch_update_stock, _batch_insert_order_lines, _batch_delete_new_orders, _batch_update_orders, _batch_update_order_lines, _batch_update_customers
- Each method ends with self.conn.commit()
- Exact param keys only (params["w_id"] → w_id, never w_w_id)
- No window functions (MySQL 5.7)
- Dynamic IN clauses use __IN_CLAUSE__ marker approach
- Batch INSERT/UPDATE/DELETE writes use `cursor.executemany(full_template, params_list)`; never pass a raw comma-separated tuple string like `(%s,%s),(%s,%s)` as the SQL
- TXN_QUERIES dict must be preserved with top-level transaction keys

Run tests: uv run python tests/ast_checker.py --auto
Generate: make gen
Benchmark: make run <drivername>
```
