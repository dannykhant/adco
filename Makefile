# Force Make to use Bash instead of the default /bin/sh
SHELL := /bin/bash

# Define shortcuts/tasks that do not generate output files
.PHONY: gen run genrun gen-generic test-unit test-tpcc clean clean-all

MODEL ?= gemini-2.5-flash
OUTPUT := tpcc/drivers

# TPC-C driver files
TARGET  := tpcc/drivers/mysqldriver.py
SUPPORT := tpcc/drivers/abstractdriver.py tpcc/constants.py tpcc/tpcc.py

gen:
	@echo "Generating optimized driver..."
	uv run python -m engine.main $(TARGET) \
		$(addprefix --with ,$(SUPPORT)) \
		--output-dir=$(OUTPUT) --model=$(MODEL)

run:
	$(eval DRIVER := $(filter-out $@,$(MAKECMDGOALS)))
	@if [ -z "$(DRIVER)" ]; then \
		echo "Usage: make run <driver_name>"; \
		echo "Example: make run baselinemysql"; \
		exit 1; \
	fi
	uv run python tpcc/tpcc.py $(DRIVER) \
		--config=tpcc/configs/mysql.config \
		--clients=1

gen-run:
	$(MAKE) gen MODEL=$(MODEL) && \
	echo "Running latest generated driver..." && \
	$(MAKE) run gemini-optimized

# Generic mode: optimize any file
gen-generic:
	@echo "Usage: make gen-generic TARGET=<file> [SUPPORT='file1 file2']"
	@echo "Example: make gen-generic TARGET=./myproject/db.py SUPPORT='./myproject/models.py ./myproject/config.py'"
	@if [ -z "$(TARGET)" ]; then \
		echo "ERROR: TARGET is required"; \
		exit 1; \
	fi
	uv run python -m engine.main $(TARGET) $(addprefix --with ,$(SUPPORT)) --model=$(MODEL)

# Prevent Make from erroring on extra args
%:
	@true

test-tpcc:
	@echo "Running integration tests..."
	uv run python tpcc/scripts/correctness_check.py \
		--config=configs/mysql.config \
		--config2=configs/mysql.config \
		--config3=configs/mysql.config \
		--warehouses=1 --transactions=500
	@echo "Tests completed."

test-unit:
	@echo "Running AST-based checker on latest generated driver..."
	uv run python tests/ast_checker.py --auto --verbose
	@echo "AST-based test completed."

clean:
	@echo "Dropping candidates database..."
	@mysql -h 127.0.0.1 -u root -pmysql_root_password -e "DROP DATABASE IF EXISTS \`tpcc-candidates\`" 2>/dev/null
	@echo "Dropping candidates database completed."

clean-all:
	@echo "Dropping all TPC-C databases..."
	@./tpcc/scripts/cleanup_db.sh
	@echo "Dropping all TPC-C databases completed."
