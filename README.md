# TPC-C Benchmark — LLM-Generated Query Optimization

This project evaluates whether LLM-generated query rewrites (via DeepSeek V4 Flash) can produce **correct and faster** SQL for TPC-C analytic queries compared to a handwritten baseline. It extends the original [`apavlo/py-tpcc`](https://github.com/apavlo/py-tpcc) TPC-C implementation with:

- Two MySQL drivers: `baselinemysql` (baseline queries) and `deepseekv4flashmysql` (LLM-rewritten queries)
- 10 analytic queries appended to the standard TPC-C workload
- Side-by-side benchmark comparison (throughput, latency, per-query timing)
- Correctness verification against the same database

## Project Structure

| Path | Purpose |
|------|---------|
| `tpcc.py` | Main entry point for standard TPC-C execution |
| `drivers/baselinemysqldriver.py` | Baseline MySQL driver (handwritten queries) |
| `drivers/deepseekv4flashmysqldriver.py` | Optimized MySQL driver (LLM-rewritten queries) |
| `scripts/benchmark_compare.py` | Side-by-side benchmark comparison |
| `scripts/correctness_check.py` | Analytic query output verification |
| `configs/` | Configuration files |
| `tpcc.mysql.sql` | Database schema |

## Setup

```bash
python tpcc.py --print-config mysql > mysql.config
# edit mysql.config with your MySQL credentials
```

## Usage

### 1. Quick Test (Single Driver)

```bash
# Load data
python tpcc.py --config=mysql.config tpcc load

# Run benchmark (no execution)
python tpcc.py --no-execute --config=mysql.config tpcc
```

### 2. Benchmark Comparison

Compare the baseline and LLM-optimized drivers side-by-side:

```bash
# Single config with both sections
python scripts/benchmark_compare.py --config=tpcc.cfg --duration=60 --clients=4 --warehouses=4

# Separate configs
python scripts/benchmark_compare.py --config=configs/baselinemysql.config \
    --config2=configs/deepseekv4flashmysql.config \
    --duration=60 --clients=4 --warehouses=4
```

Arguments:

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | (required) | Config file for `baselinemysql` |
| `--config2` | `--config` | Config file for `deepseekv4flashmysql` |
| `--duration` | 60 | Benchmark duration per run (seconds) |
| `--clients` | 1 | Number of client processes |
| `--warehouses` | 4 | Number of warehouses |
| `--scalefactor` | 1 | Scale factor |
| `--stop-on-error` | false | Stop on transaction errors |
| `--output` | none | Output JSON file for results |

Output includes: TPC-C throughput (tps, tpmC), per-transaction average latency, and per-query analytic latency in milliseconds.

### 3. Correctness Check

Verify both drivers produce identical results for each analytic query against the same database. Logs the full SQL, row count, and result data for developer inspection.

```bash
python scripts/correctness_check.py --config=configs/baselinemysql.config --warehouses=1
```

Arguments:

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | (required) | Config file (uses `[baselinemysql]` then `[mysql]` fallback) |
| `--warehouses` | 1 | Number of warehouses (use >1 for richer analytic query results) |
| `--scalefactor` | 1 | Scale factor |

### 4. Standard TPC-C (Single Driver)

```bash
# Load and execute
python tpcc.py --config=mysql.config tpcc load execute

# Customize
python tpcc.py --config=mysql.config --duration=120 --clients=8 --warehouses=10 tpcc load execute

# CSV driver (dump input data without a database)
python tpcc.py csv
```

## Config Files

Each config file supports driver-specific sections with fallback to `[mysql]`:

```ini
[mysql]
host = localhost
port = 3306
user = root
password = your_password
database = tpcc

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
database = tpcc-deepseek
```

## Extending

To add a new driver:
1. Create `drivers/yourdriver.py` with a class that extends `AbstractDriver`
2. Append 10 analytic queries as `ANALYTIC_QUERIES` with `doAnalyticsQuery()`
3. Add a config section to your config file
4. Reference it in `scripts/benchmark_compare.py`

## Credits

Based on the original `py-tpcc` by Andy Pavlo and contributors. Extended for LLM-generated query optimization benchmarking.
