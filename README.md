# adco

Evaluates whether LLM-generated query rewrites can produce **correct and faster** SQL for TPC-C transactional queries compared to a handwritten baseline and a human-optimized v2 reference. Extends [`apavlo/py-tpcc`](https://github.com/apavlo/py-tpcc) with:

- **Query Rewrite Engine** (`engine/main.py`) — one-shot generation of complete optimized MySQL drivers via Gemini
- Four MySQL drivers: `baselinemysql` (baseline), `deepseekv4flashmysql` (v1), `deepseekv4flashmysqlv2` (v2), and generated `candidates` drivers
- Side-by-side benchmark comparison (throughput, latency, per-transaction timing)
- Record-and-replay correctness verification across all drivers (same params, compared to baseline)
- Full query documentation with per-transaction SQL and round-trip comparison

## Project Structure

| Path | Purpose |
|------|---------|
| `engine/main.py` | **Query Rewrite Engine** — one-shot full driver generation via Gemini |
| `engine/.env` | `GOOGLE_API_KEY` for Gemini |
| `Makefile` | Workflow targets: `gen`, `run`, `genrun`, `test`, `clean` |
| `AGENTS.md` | Full project context, architecture patterns, known bugs |
| `tpcc/drivers/baselinemysqldriver.py` | Baseline — one query at a time, per TPC-C spec |
| `tpcc/drivers/deepseekv4flashmysqlv2driver.py` | v2 — handwritten optimized reference (used as copy-paste example in prompt) |
| `tpcc/drivers/*driver.py` | Generated candidate drivers |
| `tpcc/scripts/correctness_check.py` | Record-and-replay correctness verification |
| `tpcc/runtime/executor.py` | TPC-C workload generator — generates random transaction params |
| `tpcc/constants.py` | All TPC-C constants |
| `tpcc/tpcc.py` | Main benchmark entry point |
| `docs/kb/query_rewrite_methods.md` | Knowledge base: 5 rewrite strategies (COMBINING_QUERIES, PREDICATE_PUSHDOWN, JOIN_ORDER_HINTS, SEPARATING_QUERIES, CONCURRENCY) with TPC-C from→to examples |
| `docs/queries/queries_20260715_0930.md` | Full SQL comparison across all drivers for all 5 transactions |
| `tpcc/configs/mysql.config` | MySQL connection configs (all drivers in one file) |
| `mysql/docker-compose.yml` | MySQL 5.7 container |

## Make Targets

| Target | Description |
|--------|-------------|
| `make gen` | Generate driver with default model |
| `make gen MODEL=gemini-2.5-flash` | Generate with a custom model |
| `make run <driver>` | Benchmark a driver (10s, no load, 1 client) |
| `make gen-run` | Generate + benchmark in one step |
| `make test` | Integration test (MySQL required) |
| `make test-unit` | Unit tests (no MySQL needed) |
| `make clean` | Drop only `tpcc-candidates` database |
| `make clean-all` | Drop all TPC-C databases |

Model variable defaults to `gemini-2.5-flash`.

## Usage

### Generate an Optimized Driver

One-shot generation: reads the baseline driver and KB rewrite strategies, sends to Gemini with the v2 driver as an example, and writes a complete driver file.

```bash
# Generate with default model (gemini-2.5-flash)
make gen

# Generate with a different model
make gen MODEL=gemini-2.5-pro

# Dry-run (inspect prompt without calling API)
uv run python engine/main.py --dry-run

# Just print the output filename stem
uv run python engine/main.py --print-name
```

Output file: `tpcc/drivers/{model_name}_{timestamp}driver.py`.

### Benchmark a Driver

```bash
# Run candidates driver (generated)
uv run python tpcc/tpcc.py candidates --config=tpcc/configs/mysql.config --duration=10 --no-load --clients=1

# Run via Makefile
make run candidates

# Generate + run in one step
make genrun
```

**Note**: `--clients > 1` has a known race condition on `D_NEXT_O_ID`. Use `--clients=1`.

### Reset Database

```bash
# Drop and reload all databases
make cleanall

# Drop only the candidates database
make clean

# Load data into a specific database
uv run python tpcc/tpcc.py baselinemysql --config=tpcc/configs/mysql.config --warehouses=4 --reset
```

## Running Tests

### Unit Tests (no MySQL needed)

Tests the generated driver's structure, SQL patterns, and transaction return values using mocked MySQL cursor/connection.

```bash
make test-unit
```

Picks the latest `gemini*driver.py` automatically. Override with:

```bash
TEST_DRIVER=gemini_2_5_flash_20260717_1004 uv run pytest tests/ -v
```

### Integration Test (MySQL required)

Record-and-replay correctness verification across all databases.

```bash
make test

# Or directly:
uv run python tpcc/scripts/correctness_check.py \
    --config=configs/mysql.config \
    --config2=configs/mysql.config \
    --config3=configs/mysql.config \
    --warehouses=4 --transactions=500
```

Requires all MySQL databases to be loaded with identical data (same RNG state). See `AGENTS.md` for details.

## Config Files

Single config file with driver-specific sections:

```ini
[baselinemysql]
host = 127.0.0.1
port = 3306
user = root
password = your_password
database = tpcc-baseline

[deepseekv4flashmysqlv2]
host = 127.0.0.1
port = 3306
user = root
password = your_password
database = tpcc-deepseekv4flashv2

[candidates]
host = 127.0.0.1
port = 3306
user = root
password = your_password
database = tpcc-candidates
```

## How the Engine Works

1. **Reads** the baseline driver, v2 reference driver, and KB rewrite strategies from file
2. **Builds a prompt** structured as: KB strategies (5 abstract methods with from→to examples) → v2 example → baseline to optimize → output rules
3. **Calls Gemini** (`genai.Client().models.generate_content()`) with a one-shot prompt
4. **Validates** output for required methods (5 transaction methods), TXN_QUERIES, and 4 batch helpers
5. **Writes** the complete driver file to `tpcc/drivers/`

Generated drivers use the `[candidates]` config section (database `tpcc-candidates`).

## Round-Trip Reduction (v2 vs Baseline)

| Transaction | Baseline | v2 |
|---|---|---|
| DELIVERY (10 districts) | 70 | **5** |
| NEW_ORDER (N=10) | 46 | **8** |
| ORDER_STATUS (by c_id) | 3 | **1** |
| ORDER_STATUS (by c_last) | 3 | **2** |
| PAYMENT (by c_id) | 7 | **5** |
| PAYMENT (by c_last) | 7 | **6** |
| STOCK_LEVEL | 2 | **1** |

## Extending

To add a new driver manually:
1. Create `tpcc/drivers/<name>driver.py` with a class `<Name>Driver(AbstractDriver)`
2. Add a `[<name>]` section to `tpcc/configs/mysql.config`

To generate a driver with the engine: `make gen MODEL=<model_id>`.

## Credits

Based on the original [`apavlo/py-tpcc`](https://github.com/apavlo/py-tpcc) by Andy Pavlo and contributors. Extended for LLM-generated query optimization benchmarking.
