# Force Make to use Bash instead of the default /bin/sh
SHELL := /bin/bash

# Define shortcuts/tasks that do not generate output files
.PHONY: run test clean

run:
	@echo "Running benchmark..."
	uv run python tpcc/tpcc.py baselinemysql \
		--config=tpcc/configs/baselinemysql.config \
		--clients=1
	uv run python tpcc/tpcc.py deepseekv4flashmysql \
		--config=tpcc/configs/deepseekv4flashmysql.config \
		--clients=1
	uv run python tpcc/tpcc.py deepseekv4flashmysqlv2 \
		--config=tpcc/configs/deepseekv4flashmysqlv2.config \
		--clients=1
	@echo "Benchmark completed."

test:
	@echo "Running tests..."
	uv run python tpcc/scripts/correctness_check.py \
		--config=configs/baselinemysql.config \
		--config2=configs/deepseekv4flashmysql.config \
		--config3=configs/deepseekv4flashmysqlv2.config \
		--warehouses=1 --transactions=500
	@echo "Tests completed."

clean:
	@echo "Cleaning up databases..."
	./tpcc/scripts/cleanup_db.sh