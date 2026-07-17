# Force Make to use Bash instead of the default /bin/sh
SHELL := /bin/bash

# Define shortcuts/tasks that do not generate output files
.PHONY: gen run genrun test cleancandidates cleanall

gen:
	@echo "Generating driver..."
	uv run python engine/main.py --gemini-model=gemini-2.5-flash

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

genrun:
	@stem=$$(uv run python engine/main.py --print-name --model "gemini_2_5_flash"); \
	$(MAKE) gen && \
	echo "Running $$stem..." && \
	$(MAKE) run $$stem

# Prevent Make from erroring on extra args
%:
	@true

test:
	@echo "Running tests..."
	uv run python tpcc/scripts/correctness_check.py \
		--config=configs/mysql.config \
		--config2=configs/mysql.config \
		--config3=configs/mysql.config \
		--warehouses=1 --transactions=500
	@echo "Tests completed."

clean:
	@echo "Dropping candidates database..."
	@mysql -h 127.0.0.1 -u root -pmysql_root_password -e "DROP DATABASE IF EXISTS \`tpcc-candidates\`" 2>/dev/null
	@echo "Dropping candidates database completed."

cleanall:
	@echo "Dropping all TPC-C databases..."
	@./tpcc/scripts/cleanup_db.sh
	@echo "Dropping all TPC-C databases completed."