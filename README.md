# py-tpcc-python3

A Python 3 compatible fork of the original [`apavlo/py-tpcc`](https://github.com/apavlo/py-tpcc) TPC-C benchmark implementation.

This fork modernizes the original project by porting it to Python 3 and adding native MySQL support while preserving the original TPC-C workload implementation.

## Features

- Python 3 compatibility
- MySQL driver
- Original TPC-C benchmark workload
- Data loading and benchmark execution

## Changes from the Original

- Ported the codebase from Python 2 to Python 3
- Added a MySQL driver
- Updated dependencies for modern Python environments
- Fixed Python 3 compatibility issues and miscellaneous bugs

## Credits

This project is based on the original `py-tpcc` implementation by Andy Pavlo and contributors. This fork aims to modernize the project while remaining faithful to the original benchmark implementation.

## Quick Start

Assuming that you already have MySQL installed on you local machine, you can test this benchmark using the following commands.

Step 1: Dump out the system's configuration to a file and then make any changes you need to that file (e.g., passwords, hostname).

```
python tpcc.py --print-config mysql > mysql.config
```

Step 2: Then execute tpcc.py again to insert the TPC-C tables and data into the database and then execute the transactional workload:

```
python tpcc.py --config=mysql.config <database> load
```

Make any changes you need to 'mysql.config' (e.g., passwords, hostnames). 
Then test the loader:

```
python ./tpcc.py --no-execute --config=mysql.config mysql
``` 

You can use the CSV driver if you want to see what the data or transaction 
input parameters will look like. The following command will dump out just the 
input to the driver's functions to files in /tmp/tpcc-*

```
python ./tpcc.py csv
```

## Benchmark Comparison

Compare the baseline (`baselinemysql`) and optimized (`deepseekv4flashmysql`) drivers side-by-side.

Each driver reads the section matching its name from the config file — same as `tpcc.py`.

```
# one config file with [baselinemysql] and [deepseekv4flashmysql] sections
python scripts/benchmark_compare.py --config=tpcc.cfg --duration=60 --clients=4 --warehouses=4

# separate config files per driver
python scripts/benchmark_compare.py --config=baselinemysql.config --config2=deepseekv4flashmysql.config
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

The script loads fresh data for each driver, runs the TPC-C workload, then prints a throughput and latency comparison table.

## Correctness Check

Verify that `baselinemysql` and `deepseekv4flashmysql` analytic queries (Q1–Q10) produce identical outputs against the same database.

```
python scripts/correctness_check.py --config=configs/baselinemysql.config --warehouses=1
```

Arguments:

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | (required) | Config file (uses `[baselinemysql]` then `[mysql]` fallback) |
| `--warehouses` | 1 | Number of warehouses |
| `--scalefactor` | 1 | Scale factor |
``` 