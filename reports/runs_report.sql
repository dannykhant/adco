-- SQLite
select 
    e.id as engine_run_id,
    datetime(e.timestamp, '+7 hours') as engine_run_timestamp,
    e.model as engine_model,
    e.run_status as engine_run_status,
    e.total_duration_ms / 1000.0 as engine_total_duration_s,
    e.total_input_tokens as engine_total_input_tokens,
    e.total_output_tokens as engine_total_output_tokens,
    c.model as checker_model,
    c.run_status as checker_run_status,
    c.total_duration_ms / 1000.0 as checker_duration_s,
    c.checker_status as checker_status,
    c.failure_category as checker_failure_category,
    t.run_status as tpcc_run_status,
    t.duration_ms / 1000.0 as tpcc_duration_s,
    t.txn_status as tpcc_txn_status,
    t.missing_txns as tpcc_missing_txns,
    t.total_executed as tpcc_total_executed,
    t.total_tps as tpcc_total_tps
from engine_runs e
left join checker_runs c on e.id = c.engine_run_id
left join tpcc_runs t on e.id = t.engine_run_id;