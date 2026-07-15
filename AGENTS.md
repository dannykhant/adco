# DB Benchmarking

## Overview

TPC-C benchmark comparing handwritten SQL (`baselinemysql`) vs LLM-rewritten SQL (`deepseekv4flashmysql` v1, `deepseekv4flashmysqlv2` v2) for transactional query optimization. The goal: fewer round-trips without changing behavior.

## Key Files

| File | Purpose |
|------|---------|
| `drivers/baselinemysqldriver.py` | Reference implementation — one query at a time, per TPC-C spec |
| `drivers/deepseekv4flashmysqldriver.py` | v1 — batched/merged queries (batch IN, CASE UPDATE, merged JOINs) |
| `drivers/deepseekv4flashmysqlv2driver.py` | v2 — further batch writes, deeper merges (DELIVERY 5 RTs, ORDER_STATUS 1-2 RTs) |
| `scripts/correctness_check.py` | Record-and-replay: runs N txns through baseline, replays same params through v1 and v2 |
| `docs/queries/queries_20260715_0930.md` | Full SQL comparison across all 3 drivers for all 5 transactions |
| `docs/kb/query_rewrite_methods.md` | Knowledge base with COMBINING_QUERIES, PREDICATE_PUSHDOWN strategies |
| `runtime/executor.py` | TPC-C workload generator — generates random transaction params |
| `configs/` | MySQL connection configs per driver |
| `constants.py` | All TPC-C constants (table sizes, ranges, thresholds, strings) |

## How to Run

```bash
# Correctness check (500 transactions, all 3 drivers)
uv run python scripts/correctness_check.py \
    --config=configs/baselinemysql.config \
    --config2=configs/deepseekv4flashmysql.config \
    --config3=configs/deepseekv4flashmysqlv2.config \
    --warehouses=4 --transactions=500

# Standalone benchmark (30 seconds, skip data load, single client)
uv run python tpcc.py deepseekv4flashmysqlv2 --config=configs/deepseekv4flashmysqlv2.config \
    --duration=30 --no-load --clients=1

# Reset database and load data (4 warehouses)
uv run python tpcc.py deepseekv4flashmysqlv2 --config=configs/deepseekv4flashmysqlv2.config \
    --warehouses=4 --reset
```

**Note**: `--clients > 1` has a known race condition on `D_NEXT_O_ID`. Use `--clients=1`.

## Transaction Methods

All 3 drivers implement these 5 methods, returning identical types:

| Method | Params | Return Value |
|--------|--------|-------------|
| `doDelivery(params)` | `{w_id, o_carrier_id, ol_delivery_d}` | `list[(d_id, no_o_id)]` — one per district with pending orders |
| `doNewOrder(params)` | `{w_id, d_id, c_id, o_entry_d, i_ids[], i_w_ids[], i_qtys[]}` | `[customer_info, misc, item_data]` |
| `doOrderStatus(params)` | `{w_id, d_id, c_id, c_last}` | `[customer, order_or_None, orderLines_or_[]]` |
| `doPayment(params)` | `{w_id, d_id, h_amount, c_w_id, c_d_id, c_id, c_last, h_date}` | `[warehouse, district, customer]` |
| `doStockLevel(params)` | `{w_id, d_id, threshold}` | `int` — count of distinct low-stock items |

## Important Context

### Databases
- Three separate MySQL databases: `tpcc-baseline`, `tpcc-deepseekv4flash`, `tpcc-deepseekv4flashv2`
- All drivers share the same schema (`tpcc.mysql.sql`)
- All use `MySQLdb` (imported as `mysql`) with `%s` parameter binding; falls back to `pymysql`
- Default: 4 warehouses, 10 districts per warehouse, 3000 customers per district

### Transaction Mix (randomized via `rand` module)
| Transaction | Frequency |
|-------------|-----------|
| NEW_ORDER | 45% |
| PAYMENT | 43% |
| DELIVERY | 4% |
| ORDER_STATUS | 4% |
| STOCK_LEVEL | 4% |

- NEW_ORDER has 5-15 line items per call
- DELIVERY loops over all 10 districts (finds one pending order per district)

### Data Loading Determinism
- `rand` module uses Python's global `random` state
- For correctness testing, ALL databases must be loaded with the SAME RNG state
- Save/restore pattern: `rng_state = random.getstate()` before first load, `random.setstate(rng_state)` before subsequent loads

## Architecture Patterns

### Baseline Driver Pattern
- Each query is an isolated `cursor.execute()` call
- Per-item loops use N individual queries (e.g., 5-15 SELECTs for stock info)
- All writes are individual UPDATE/INSERT statements

### Optimized (Deepseek) Driver Pattern
- **Query batching**: `WHERE I_ID IN (%s)` replaces N individual item lookups
- **Query merging**: `JOIN` across 3 tables replaces 3 separate queries (warehouse, district, customer)
- **Batch writes**: Single `UPDATE` with `CASE` expressions replaces N individual stock updates
- **Multi-row INSERT**: Single INSERT with multiple VALUES tuples replaces N inserts

### v2 Specific Optimizations
- **DELIVERY**: Inline sumOLAmount as correlated subquery + 4 batch writes (5 RTs vs 70 baseline)
- **ORDER_STATUS**: Merge order + lines into one query via derived table/subquery (1 RT c_id, 2 RT c_last)
- **PAYMENT**: Merge customer + warehouse + district into one 3-table comma join (5 RTs c_id)
- **STOCK_LEVEL**: Derived table for district bounds + JOIN instead of duplicated subquery + EXISTS

### Batch Update SQL Pitfall (KNOWN BUG — DO NOT REPEAT)
The `_batch_update_stock()` method uses a single SQL with 4 separate `CASE` blocks (one per column). **Params must be ordered column-major, not row-major.** The SQL reads ALL params for S_QUANTITY first, then ALL for S_YTD, etc. — so params must be grouped as:
```
quantity_params + ytd_params + order_cnt_params + remote_cnt_params + where_params
```
NOT interleaved per row (which would assign S_YTD values to S_QUANTITY's CASE WHEN).

### Batch Write Cross-Warehouse Guard (DELIVERY)
All 4 batch write helpers (`_batch_delete_new_orders`, `_batch_update_orders`, `_batch_update_order_lines`, `_batch_update_customers`) include a `w_id` filter (`NO_W_ID = %s`, `O_W_ID = %s`, `OL_W_ID = %s`, `C_W_ID = %s`). Without it, `(d_id, o_id)` pairs are only unique per-warehouse and would corrupt other warehouses.

### Stock Quantity Logic (TPC-C 2.5.1.3)
Every NEW_ORDER applies this to each stock row:
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

### Config File Format
```ini
[driver-name]
host = 127.0.0.1
port = 3306
user = root
password = your_password
database = tpcc-baseline
```
Used configs: `configs/baselinemysql.config`, `configs/deepseekv4flashmysql.config`, `configs/deepseekv4flashmysqlv2.config`

Each config file may also contain a `[deepseekv4flashmysqlv2]` fallback section for convenience.

## Query Rewrite Knowledge Base

When performing query rewrite or optimization tasks, you MUST first read `docs/kb/query_rewrite_methods.md` for reference strategies and patterns.

## Query Documentation

The `docs/queries/queries_20260715_0930.md` file documents every baseline, v1, and v2 query per transaction, including SQL, rationale for each merge/batch decision, and round-trip count comparison.

## Correctness Check Limitations

- `datetime` fields are skipped during comparison (differ between record/replay due to real time)
- Float comparison uses 0.001 tolerance
- All databases must be loaded with same RNG state (already handled by the script)
- Known: STOCK_LEVEL count differences of 1 are expected edge cases (S_QUANTITY values near threshold boundary can differ if prior NEW_ORDER stock updates diverged)
