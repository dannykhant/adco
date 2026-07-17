"""
Query Rewrite Engine — One-Shot Full Driver Generation

Reads the baseline MySQL driver, applies rewrite strategies from
docs/kb/query_rewrite_methods.md, and generates a complete optimized
driver file via Gemini. The prompt includes correct boilerplate and
helper code as copy-paste reference, so the LLM only writes the
SQL queries and transaction orchestration logic.

Usage:
    uv run python engine/main.py [--model MODEL_NAME]
"""

import argparse
import datetime
import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_PATH = os.path.join(ROOT_DIR, "docs", "kb", "query_rewrite_methods.md")
TPCC_DIR = os.path.join(ROOT_DIR, "tpcc")
BASELINE_PATH = os.path.join(TPCC_DIR, "drivers", "baselinemysqldriver.py")
V2_PATH = os.path.join(TPCC_DIR, "drivers", "deepseekv4flashmysqlv2driver.py")
DRIVERS_DIR = os.path.join(TPCC_DIR, "drivers")


def build_prompt(baseline_code, v2_full_code, rewrite_strategies, model_name):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    version_name = f"{model_name.lower()}_{timestamp}"
    class_name = version_name.title()

    return f"""You are a TPC-C query rewrite optimizer. Generate a COMPLETE optimized MySQL driver that outperforms the v2 reference.

## REWRITE STRATEGIES — Study and Apply

Use these strategies to transform the baseline. The v2 driver below is one concrete example of applying them.

{rewrite_strategies}

## EXAMPLE: v2 DRIVER (one implementation of these strategies)

Below is the complete v2 driver. It demonstrates all the strategies above in a working implementation.
Study how v2 applies each strategy, then improve upon it for your driver.

- v2's DELIVERY uses COMBINING_QUERIES (per-district loop → one batch) → 5 RTs
- v2's NEW_ORDER uses COMBINING_QUERIES (N sequential lookups → batched reads + JOINs + batched writes) → ~8 RTs
- v2's ORDER_STATUS uses COMBINING_QUERIES (3 sequential queries → 1 merged LEFT JOIN) → 1-2 RTs
- v2's PAYMENT uses COMBINING_QUERIES (3 sequential queries → 1 three-table comma join) → 4-6 RTs
- v2's STOCK_LEVEL uses COMBINING_QUERIES (2 queries → 1 with derived table) → 1 RT

```python
{v2_full_code}
```

## YOUR TASK: Optimize the Baseline Driver

Apply the rewrite strategies to the BASELINE code below. Your driver should:

1. **Copy all boilerplate and helpers from v2 verbatim** — imports, DEFAULT_CONFIG,
   __init__, loadConfig, _execute_ddl, loadTuples, loadFinish, and all 8 batch helpers.
   These are correct — do not modify them.

2. **Write your own TXN_QUERIES dict** — optimized SQL queries applying the strategies.
   TXN_QUERIES keys are free (you define them; your methods call whatever keys exist).

3. **Write your own 5 transaction methods** — orchestration logic applying the strategies.

4. **Aim to outperform v2** — fewer round-trips, smarter merges, better SQL.
   Can you merge more? Can you batch more? Can you find opportunities v2 missed?

## RULES

- Class: `{class_name}Driver(AbstractDriver)`, driver name: `"candidates"`
- All `%s` in TXN_QUERIES are cursor.execute placeholders (NO `%%s`)
- Helpers use `%%s` for surviving cursor.execute placeholders (copy from v2 — already correct)
- Batch update stock params are column-major: `quantity_params + ytd_params + order_cnt_params + remote_cnt_params + where_params`
- All DELIVERY batch writes must include w_id filter (NO_W_ID, O_W_ID, OL_W_ID, C_W_ID)

## RETURN VALUES
- doDelivery: list[(d_id, no_o_id)]
- doNewOrder: return [customer_info, misc, item_data] or None on missing items
- doOrderStatus: [customer, order_or_None, orderLines_or_[]]
- doPayment: [warehouse, district, customer]
- doStockLevel: int

## STOCK QUANTITY LOGIC (TPC-C 2.5.1.3 — apply per item)
s_ytd += ol_quantity
if s_quantity >= ol_quantity + 10: s_quantity -= ol_quantity
else: s_quantity += 91 - ol_quantity
s_order_cnt += 1
if ol_supply_w_id != w_id: s_remote_cnt += 1

## BASELINE DRIVER

```python
{baseline_code}
```

Generate the COMPLETE optimized driver now. Output ONLY Python code, no explanation.
"""


def main():
    parser = argparse.ArgumentParser(description="Query rewrite engine — one-shot full driver generation")
    parser.add_argument("--model", default="gemini-2.5-flash",
                        help="Gemini model ID and driver name stem (hyphens/dots become underscores)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-name", action="store_true")
    args = parser.parse_args()

    model_name = args.model.replace("-", "_").replace(".", "_")
    baseline_code = open(BASELINE_PATH).read()
    v2_code = open(V2_PATH).read()
    rewrite_strategies = open(KB_PATH).read()
    prompt = build_prompt(baseline_code, v2_code, rewrite_strategies, model_name)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    version_name = f"{model_name.lower()}_{timestamp}"
    output_path = os.path.join(DRIVERS_DIR, f"{version_name}driver.py")

    if args.print_name:
        print(version_name)
        return

    if args.dry_run:
        print(prompt)
        return

    client = genai.Client()
    print(f"  Model:   {args.model}")
    print(f"  Output:  {output_path}")
    print("  Generating...", end=" ", flush=True)

    response = client.models.generate_content(
        model=args.model,
        contents=prompt,
    )

    code = response.text.strip()
    if code.startswith("```"):
        code = code.split("\n", 1)[1]
        code = code.rsplit("```", 1)[0]
        code = code.strip()

    with open(output_path, "w") as f:
        f.write(code)
        if not code.endswith("\n"):
            f.write("\n")

    missing = []
    for method in ["doDelivery", "doNewOrder", "doOrderStatus", "doPayment", "doStockLevel"]:
        if f"def {method}(" not in code:
            missing.append(method)
    if "TXN_QUERIES" not in code:
        missing.append("TXN_QUERIES")
    for helper in ["_batch_items", "_batch_stock_info", "_batch_update_stock", "_batch_delete_new_orders"]:
        if f"def {helper}(" not in code:
            missing.append(helper)

    print("done")
    print(f"  Generated {len(code)} bytes -> {output_path}")
    if missing:
        print(f"  WARNING — missing: {', '.join(missing)}")
    else:
        print("  All required components present.")


if __name__ == "__main__":
    main()
