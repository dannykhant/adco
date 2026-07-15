# Query Rewrite Optimization

This project evaluates whether LLM-generated query rewrites (via DeepSeek V4 Flash) can produce **correct and faster** SQL for TPC-C transactional queries compared to a handwritten baseline. It extends the original [`apavlo/py-tpcc`](https://github.com/apavlo/py-tpcc) TPC-C implementation with:

- Three MySQL drivers: `baselinemysql` (baseline), `deepseekv4flashmysql` (v1), and `deepseekv4flashmysqlv2` (v2 — further optimized)
- Side-by-side benchmark comparison (throughput, latency, per-transaction timing)
- Record-and-replay correctness verification across all drivers (same params, compared to baseline)
- Full query documentation with per-transaction SQL and round-trip comparison

## Project Structure

| Path | Purpose |
|------|---------|
| `tpcc.py` | Main entry point for standard TPC-C execution |
| `drivers/baselinemysqldriver.py` | Baseline — one query at a time, per TPC-C spec |
| `drivers/deepseekv4flashmysqldriver.py` | v1 — batched/merged queries (batch IN, CASE UPDATE, merged JOINs) |
| `drivers/deepseekv4flashmysqlv2driver.py` | v2 — further batch writes, deeper merges (DELIVERY 5 RTs, ORDER_STATUS 1-2 RTs) |
| `scripts/correctness_check.py` | Record-and-replay: runs N txns through baseline, replays same params through v1 and v2 |
| `docs/queries/queries_20260715_0930.md` | Full SQL comparison across all 3 drivers for all 5 transactions |
| `docs/kb/query_rewrite_methods.md` | Knowledge base of query rewrite strategies (COMBINING_QUERIES, PREDICATE_PUSHDOWN, etc.) |
| `configs/` | Configuration files per driver |
| `tpcc.mysql.sql` | Database schema |

## Usage

### Correctness Check (Record-and-Replay)

Loads identical data into all databases, records N transactions from baselinemysql, then replays the same params through v1 and v2 and compares results.

```bash
uv run python scripts/correctness_check.py \
    --config=configs/baselinemysql.config \
    --config2=configs/deepseekv4flashmysql.config \
    --config3=configs/deepseekv4flashmysqlv2.config \
    --warehouses=4 --transactions=500
```

Arguments:

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | (required) | Config file for `baselinemysql` |
| `--config2` | `--config` | Config for `deepseekv4flashmysql` |
| `--config3` | `--config` | Config for `deepseekv4flashmysqlv2` |
| `--warehouses` | 4 | Number of warehouses |
| `--scalefactor` | 1 | Scale factor |
| `--transactions` | 500 | Number of transactions to record and replay |
| `--stop-on-error` | false | Stop on first mismatch |

Output:
```
deepseekv4flashmysql:  500/500 passed, 0 failed
deepseekv4flashmysqlv2: 500/500 passed, 0 failed
ALL DRIVERS MATCH BASELINE
```

### Standalone Benchmark

```bash
# Load + run (4 warehouses, 30s, v2 driver)
uv run python tpcc.py deepseekv4flashmysqlv2 --config=configs/deepseekv4flashmysqlv2.config \
    --duration=30

# Run only (skip data load, 1 client)
uv run python tpcc.py deepseekv4flashmysqlv2 --config=configs/deepseekv4flashmysqlv2.config \
    --duration=30 --no-load --clients=1

# Reset database and reload
uv run python tpcc.py baselinemysql --config=configs/baselinemysql.config \
    --warehouses=4 --reset
```

**Note**: `--clients > 1` has a known race condition on `D_NEXT_O_ID` (no locking). Use `--clients=1` for reliable results.

## Config Files

Each config file supports driver-specific sections:

```ini
[baselinemysql]
host = localhost
port = 3306
user = root
password = your_password
database = tpcc-baseline

[deepseekv4flashmysql]
host = localhost
port = 3306
user = root
password = your_password
database = tpcc-deepseekv4flash

[deepseekv4flashmysqlv2]
host = localhost
port = 3306
user = root
password = your_password
database = tpcc-deepseekv4flashv2
```

## Round-Trip Reduction (v2 vs Baseline)

| Transaction | Baseline | v1 | v2 |
|---|---|---|---|
| DELIVERY (10 districts) | 70 | ~26-51 | **5** |
| NEW_ORDER (N=10) | 46 | 8 | **8** |
| ORDER_STATUS (by c_id) | 3 | 2 | **1** |
| ORDER_STATUS (by c_last) | 3 | 3 | **2** |
| PAYMENT (by c_id) | 7 | 6 | **5** |
| PAYMENT (by c_last) | 7 | 7 | **6** |
| STOCK_LEVEL | 2 | 1 | **1** |

## Extending

To add a new driver:
1. Create `drivers/<name>driver.py` with a class named `<Name>Driver` extending `AbstractDriver`
2. Add a config section to your config file (driver auto-discovers via glob)

## Credits

Based on the original `py-tpcc` by Andy Pavlo and contributors. Extended for LLM-generated query optimization benchmarking.
