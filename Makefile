# Force Make to use Bash instead of the default /bin/sh
SHELL := /bin/bash

# Define shortcuts/tasks that do not generate output files
.PHONY: gen run check gen-run chain clean clean-all

MODEL ?= gemini-2.5-flash
OUTPUT := tpcc/drivers

# TPC-C driver files
TARGET  := tpcc/drivers/mysqldriver.py
SUPPORT := tpcc/drivers/abstractdriver.py tpcc/constants.py tpcc/tpcc.py

gen:
	@echo "Generating optimized driver..."
	uv run python -m engine.main tpcc/drivers/mysqldriver.py \
		--runner tpcc/tpcc.py \
		--with tpcc/drivers/abstractdriver.py --with tpcc/constants.py \
		--output-dir=tpcc/drivers \
		--model=gemini-3.5-flash-lite

run:
	@echo "Running latest generated driver..."
	uv run python tpcc/scripts/record_run.py optimizedmysql \
		--config=tpcc/configs/mysql.config \
		--clients=1 \
		--warehouses=1 \
		--duration=60 \

baseline:
	@echo "Running baseline driver..."
	uv run python tpcc/tpcc.py mysql \
		--config=tpcc/configs/mysql.config \
		--clients=1 \
		--warehouses=1 \
		--duration=60 \

check:
	@echo "Running correctness checker on a specific file..."
	uv run python -m checker tpcc/drivers/optimizedmysqldriver.py \
		--model gemini-3.5-flash-lite

clean:
	@echo "Dropping candidates database..."
	@mysql -h 127.0.0.1 -u root -pmysql_root_password -e "DROP DATABASE IF EXISTS \`tpcc-candidates\`" 2>/dev/null
	@echo "Dropping candidates database completed."

clean-all:
	@echo "Dropping all TPC-C databases..."
	@./tpcc/scripts/cleanup_db.sh
	@echo "Dropping all TPC-C databases completed."

chain:
	@echo "=== ADCo Pipeline ==="
	@$(MAKE) gen || true
	@$(MAKE) check || true
	@$(MAKE) run || true
	@$(MAKE) clean || true
	@echo "=== Pipeline complete ==="
