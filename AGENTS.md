# adco

## Overview

TPC-C benchmark comparing handwritten SQL (`baselinemysql`) vs LLM-rewritten SQL for transactional query optimization. Uses a query rewrite engine (`engine/main.py`) that generates complete optimized MySQL drivers via Gemini in one shot.

## Key Files

| File | Purpose |
|------|---------|
| `engine/main.py` | **Query Rewrite Engine** — one-shot full driver generation via Gemini |
| `engine/.env` | `GOOGLE_API_KEY` for Gemini authentication |
| `Makefile` | Workflow targets: `gen`, `run`, `genrun`, `test`, `clean`, `cleanall` |
| `tpcc/drivers/baselinemysqldriver.py` | Baseline — one query at a time, per TPC-C spec |
| `tpcc/drivers/deepseekv4flashmysqlv2driver.py` | v2 — handwritten optimized driver (used as example in prompt) |
| `tpcc/drivers/gemini_*driver.py` | Generated candidate drivers |
| `tpcc/scripts/correctness_check.py` | Record-and-replay correctness verification |
| `tpcc/runtime/executor.py` | TPC-C workload generator |
| `tpcc/constants.py` | All TPC-C constants (table sizes, ranges, thresholds, strings) |
| `tpcc/tpcc.py` | Main benchmark entry point |
| `tpcc/configs/mysql.config` | MySQL connection configs per driver |
| `docs/kb/query_rewrite_methods.md` | Knowledge base: 5 rewrite strategies with TPC-C from→to code examples |
| `docs/queries/queries_20260715_0930.md` | Full SQL comparison across baseline, v1, v2 for all 5 transactions |

## How to Run

```bash
# Generate optimized driver
make gen                              # default gemini-2.5-flash
make gen MODEL=gemini-2.5-pro         # custom model

# Dry-run (inspect prompt without API call)
uv run python engine/main.py --dry-run

# Benchmark a driver (10s, no load, single client)
uv run python tpcc/tpcc.py candidates --config=tpcc/configs/mysql.config --duration=10 --no-load --clients=1
make run candidates                   # shorthand

# Generate + benchmark in one step
make genrun

# Reset database
make cleanall                         # drop all databases
make clean                            # drop only tpcc-candidates
```

**Note**: `--clients > 1` has a known race condition on `D_NEXT_O_ID`. Use `--clients=1`.

**Note**: Generated drivers use the `[candidates]` section in `tpcc/configs/mysql.config` (database `tpcc-candidates`).

## Transaction Methods

All drivers implement these 5 methods with identical return types:

| Method | Params | Return Value |
|--------|--------|-------------|
| `doDelivery(params)` | `{w_id, o_carrier_id, ol_delivery_d}` | `list[(d_id, no_o_id)]` — one per district with pending orders |
| `doNewOrder(params)` | `{w_id, d_id, c_id, o_entry_d, i_ids[], i_w_ids[], i_qtys[]}` | `[customer_info, misc, item_data]` or `None` on missing items |
| `doOrderStatus(params)` | `{w_id, d_id, c_id, c_last}` | `[customer, order_or_None, orderLines_or_[]]` |
| `doPayment(params)` | `{w_id, d_id, h_amount, c_w_id, c_d_id, c_id, c_last, h_date}` | `[warehouse, district, customer]` |
| `doStockLevel(params)` | `{w_id, d_id, threshold}` | `int` — count of distinct low-stock items |

## Important Context

### Databases
- Separate MySQL databases: `tpcc-baseline`, `tpcc-deepseekv4flashv2`
- Generated drivers use `tpcc-candidates` via the `[candidates]` config section
- All drivers share the same schema (`tpcc/tpcc.mysql.sql`)
- All use `MySQLdb` (imported as `mysql`) with `%s` parameter binding; falls back to `pymysql`
- Default: 4 warehouses, 10 districts per warehouse, 3000 customers per district

### Transaction Mix
| Transaction | Frequency |
|-------------|-----------|
| NEW_ORDER | 45% |
| PAYMENT | 43% |
| DELIVERY | 4% |
| ORDER_STATUS | 4% |
| STOCK_LEVEL | 4% |

### Data Loading Determinism
- `rand` module uses Python's global `random` state
- For correctness testing, ALL databases must be loaded with the SAME RNG state
- Save/restore pattern: `rng_state = random.getstate()` before first load, `random.setstate(rng_state)` before subsequent loads

## Architecture Patterns

### Baseline Driver Pattern
- Each query is an isolated `cursor.execute()` call
- Per-item loops use N individual queries (e.g., 5-15 SELECTs for stock info)
- All writes are individual UPDATE/INSERT statements

### Optimized Driver Pattern
- **Query batching**: `WHERE I_ID IN (%s)` replaces N individual item lookups
- **Query merging**: `JOIN` across 3 tables replaces 3 separate queries (warehouse, district, customer)
- **Batch writes**: Single `UPDATE` with `CASE` expressions replaces N individual stock updates
- **Multi-row INSERT**: Single INSERT with multiple VALUES tuples replaces N inserts

### v2 Specific Optimizations
- **DELIVERY**: Inline sumOLAmount as correlated subquery + 4 batch writes (5 RTs vs 70 baseline)
- **ORDER_STATUS**: Merge order + lines into one query via derived table/subquery (1 RT c_id, 2 RT c_last)
- **PAYMENT**: Merge customer + warehouse + district into one 3-table comma join (5 RTs c_id)
- **STOCK_LEVEL**: Derived table for district bounds + JOIN instead of duplicated subquery + EXISTS

## One-Shot Generation Architecture

The engine (`engine/main.py`) generates a complete standalone driver file via Gemini in one shot.

### Prompt Structure (3 sections)
1. **Rewrite Strategies** — loaded from `docs/kb/query_rewrite_methods.md` at runtime. 5 abstract strategies (COMBINING_QUERIES, PREDICATE_PUSHDOWN, JOIN_ORDER_HINTS, SEPARATING_QUERIES, CONCURRENCY), each with a TPC-C from→to code example
2. **v2 Example** — complete v2 driver shown as one concrete implementation of the strategies, annotated with which strategies it uses where
3. **Task: Optimize Baseline** — apply strategies to the baseline independently, aiming to outperform v2

### Key Rules in Prompt
- Copy all boilerplate and 8 batch helpers from v2 verbatim
- Write own TXN_QUERIES dict and 5 transaction methods
- `%%s` in helpers, `%s` in TXN_QUERIES
- Column-major params for `_batch_update_stock`
- All DELIVERY batch writes must include w_id filter

### Validation
Engine checks output for: `doDelivery`, `doNewOrder`, `doOrderStatus`, `doPayment`, `doStockLevel`, `TXN_QUERIES`, `_batch_items`, `_batch_stock_info`, `_batch_update_stock`, `_batch_delete_new_orders`.

## Known Bugs — DO NOT REPEAT

### Batch Update Stock — Column-Major Params
`_batch_update_stock()` SQL has 4 CASE blocks (one per column). **Params must be ordered column-major:**
```
quantity_params + ytd_params + order_cnt_params + remote_cnt_params + where_params
```
NOT interleaved per row.

### DELIVERY Batch Write Cross-Warehouse Guard
All 4 batch write helpers must include a `w_id` filter (`NO_W_ID = %s`, `O_W_ID = %s`, `OL_W_ID = %s`, `C_W_ID = %s`). Without it, `(d_id, o_id)` pairs collide across warehouses.

### Stock Quantity Logic (TPC-C 2.5.1.3)
```python
s_ytd += ol_quantity
if s_quantity >= ol_quantity + 10:
    s_quantity = s_quantity - ol_quantity
else:
    s_quantity = s_quantity + 91 - ol_quantity
s_order_cnt += 1
if ol_supply_w_id != w_id:
    s_remote_cnt += 1
```

## Config File Format

```ini
[driver-name]
host = 127.0.0.1
port = 3306
user = root
password = your_password
database = tpcc-baseline
```

Generated drivers use `[candidates]` in `tpcc/configs/mysql.config` (database `tpcc-candidates`).

## Round-Trip Counts (v2 vs Baseline)

| Transaction | Baseline | v2 |
|---|---|---|
| DELIVERY (10 districts) | 70 | 5 |
| NEW_ORDER (N=10) | 46 | 8 |
| ORDER_STATUS (by c_id) | 3 | 1 |
| ORDER_STATUS (by c_last) | 3 | 2 |
| PAYMENT (by c_id) | 7 | 5 |
| PAYMENT (by c_last) | 7 | 6 |
| STOCK_LEVEL | 2 | 1 |

## Correctness Check Limitations

- `datetime` fields skipped during comparison (differ between record/replay due to real time)
- Float comparison uses 0.001 tolerance
- All databases must be loaded with same RNG state (handled by script)
- Known: STOCK_LEVEL count differences of 1 are expected edge cases
