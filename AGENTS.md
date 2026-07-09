# DB Benchmarking

## Overview

TPC-C benchmark comparing handwritten SQL (`baselinemysql`) vs LLM-rewritten SQL (`deepseekv4flashmysql`) for transactional query optimization. The goal: fewer round-trips without changing behavior.

## Key Files

| File | Purpose |
|------|---------|
| `drivers/baselinemysqldriver.py` | Reference implementation — one query at a time, per TPC-C spec |
| `drivers/deepseekv4flashmysqldriver.py` | Optimized implementation — batched/merged queries |
| `scripts/correctness_check.py` | Record-and-replay: runs N txns through baseline, replays same params through deepseek, compares results |
| `queries.md` | Detailed breakdown of baseline vs optimized queries per transaction |
| `runtime/executor.py` | TPC-C workload generator — generates random transaction params |
| `configs/` | MySQL connection configs per driver |
| `constants.py` | All TPC-C constants (table sizes, ranges, thresholds, strings) |

## How to Run

```bash
# Correctness check (500 transactions)
uv run python scripts/correctness_check.py \
    --config=configs/baselinemysql.config \
    --config2=configs/deepseekv4flashmysql.config \
    --warehouses=4 --transactions=500

# Standalone benchmark (30 seconds, skip data load, single client)
uv run python tpcc.py deepseekv4flashmysql --config=configs/deepseekv4flashmysql.config \
    --duration=30 --no-load --clients=1

# Reset database and load data (4 warehouses)
uv run python tpcc.py deepseekv4flashmysql --config=configs/deepseekv4flashmysql.config \
    --warehouses=4 --reset
```

## Transaction Methods

Both drivers implement these 5 methods, all returning identical types:

| Method | Params | Return Value |
|--------|--------|-------------|
| `doDelivery(params)` | `{w_id, o_carrier_id, ol_delivery_d}` | `list[(d_id, no_o_id)]` — one per district with pending orders |
| `doNewOrder(params)` | `{w_id, d_id, c_id, o_entry_d, i_ids[], i_w_ids[], i_qtys[]}` | `[customer_info, misc, item_data]` |
| `doOrderStatus(params)` | `{w_id, d_id, c_id, c_last}` | `[customer, order_or_None, orderLines_or_[]]` |
| `doPayment(params)` | `{w_id, d_id, h_amount, c_w_id, c_d_id, c_id, c_last, h_date}` | `[warehouse, district, customer]` |
| `doStockLevel(params)` | `{w_id, d_id, threshold}` | `int` — count of distinct low-stock items |

## Important Context

### Database
- Two separate MySQL databases: `tpcc-baseline` and `tpcc-deepseekv4flash`
- Both drivers share the same schema (`tpcc.mysql.sql`)
- Both use `MySQLdb` (imported as `mysql`) with `%s` parameter binding; falls back to `pymysql`
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
- Each call to `doOne()` (in executor) generates fresh params using Python's `random` module

### Data Loading Determinism
- `rand` module uses Python's global `random` state
- For correctness testing, BOTH databases must be loaded with the SAME RNG state
- Save/restore pattern: `rng_state = random.getstate()` before first load, `random.setstate(rng_state)` before second load

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

### Batch Update SQL Pitfall (KNOWN BUG — DO NOT REPEAT)
The `_batch_update_stock()` method uses a single SQL with 4 separate `CASE` blocks (one per column). **Params must be ordered column-major, not row-major.** The SQL reads ALL params for S_QUANTITY first, then ALL for S_YTD, etc. — so params must be grouped as:
```
quantity_params + ytd_params + order_cnt_params + remote_cnt_params + where_params
```
NOT interleaved per row (which would assign S_YTD values to S_QUANTITY's CASE WHEN).

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
Used configs: `configs/baselinemysql.config`, `configs/deepseekv4flashmysql.config`

## Query Rewrite Knowledge Base

When performing query rewrite or optimization tasks, you MUST first read `docs/kb/query_rewrite_methods.md` for reference strategies and patterns.

## Query Documentation

The `queries.md` file documents every baseline and optimized query per transaction, including the rationale for each merge/batch decision and the round-trip count comparison.

## Correctness Check Limitations

- `datetime` fields are skipped during comparison (differ between record/replay due to real time)
- Float comparison uses 0.001 tolerance
- Both databases must be loaded with same RNG state (already handled by the script)
- Known: STOCK_LEVEL count differences of 1 are expected edge cases (S_QUANTITY values near threshold boundary can differ if prior NEW_ORDER stock updates diverged)
