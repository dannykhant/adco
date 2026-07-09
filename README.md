# TPC-C Benchmark — LLM-Generated Query Optimization

This project evaluates whether LLM-generated query rewrites (via DeepSeek V4 Flash) can produce **correct and faster** SQL for TPC-C transactional queries compared to a handwritten baseline. It extends the original [`apavlo/py-tpcc`](https://github.com/apavlo/py-tpcc) TPC-C implementation with:

- Two MySQL drivers: `baselinemysql` (baseline queries) and `deepseekv4flashmysql` (LLM-rewritten queries)
- Side-by-side benchmark comparison (throughput, latency, per-transaction timing)
- Record-and-replay correctness verification across both drivers

## Project Structure

| Path | Purpose |
|------|---------|
| `tpcc.py` | Main entry point for standard TPC-C execution |
| `drivers/baselinemysqldriver.py` | Baseline MySQL driver (handwritten queries) |
| `drivers/deepseekv4flashmysqldriver.py` | Optimized MySQL driver (LLM-rewritten queries) |
| `scripts/correctness_check.py` | Record-and-replay transaction correctness check |
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

### 2. Correctness Check (Record-and-Replay)

Loads identical data into both databases, records N transactions from baselinemysql, then replays the same params through deepseekv4flashmysql and compares results.

```bash
uv run python scripts/correctness_check.py \
    --config=configs/baselinemysql.config \
    --config2=configs/deepseekv4flashmysql.config \
    --warehouses=4 --transactions=500
```

Arguments:

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | (required) | Config file for `baselinemysql` |
| `--config2` | `--config` | Config file for `deepseekv4flashmysql` |
| `--warehouses` | 4 | Number of warehouses |
| `--scalefactor` | 1 | Scale factor |
| `--transactions` | 500 | Number of transactions to record and replay |
| `--stop-on-error` | false | Stop on first mismatch |

Output: `Correctness: N/N passed, 0 failed` — exits 0 on full match, 1 on any mismatch.

### 3. Standard TPC-C (Single Driver)

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
2. Add a config section to your config file

## Credits

Based on the original `py-tpcc` by Andy Pavlo and contributors. Extended for LLM-generated query optimization benchmarking.
