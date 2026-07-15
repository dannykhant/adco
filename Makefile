# Force Make to use Bash instead of the default /bin/sh
SHELL := /bin/bash

# Define shortcuts/tasks that do not generate output files
.PHONY: hello greet loop multi-line

run:
	@echo "Running benchmark..."
	uv run python tpcc.py baselinemysql \
		--config=configs/baselinemysql.config \
		--clients=1
	uv run python tpcc.py deepseekv4flashmysql \
		--config=configs/deepseekv4flashmysql.config \
		--clients=1
	uv run python tpcc.py deepseekv4flashmysqlv2 \
		--config=configs/deepseekv4flashmysqlv2.config \
		--clients=1
	@echo "Benchmark completed."

test:
	@echo "Running tests..."
	uv run python scripts/correctness_check.py \
		--config=configs/baselinemysql.config \
		--config2=configs/deepseekv4flashmysql.config \
		--config3=configs/deepseekv4flashmysqlv2.config \
		--warehouses=1 --transactions=500
	@echo "Tests completed."

clean:
	@echo "Cleaning up databases..."
	./scripts/cleanup_db.sh