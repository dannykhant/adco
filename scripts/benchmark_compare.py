#!/usr/bin/env python
"""
TPC-C Benchmark Comparison: baselinemysql vs deepseekv4flashmysql

Usage:
    # one config file with [baselinemysql] and [deepseekv4flashmysql] sections
    python scripts/benchmark_compare.py --config=tpcc.cfg

    # separate config files per driver
    python scripts/benchmark_compare.py --config=baselinemysql.config --config2=deepseekv4flashmysql.config

Each config file (or section) is read the same way tpcc.py does:
the tool reads the section matching the driver name from that file.

Flags:
    --config       Config file for first driver (baselinemysql)
    --config2      Config file for second driver (deepseekv4flashmysql).
                   Defaults to --config when omitted.
    --duration     Benchmark duration per run in seconds (default: 60)
    --clients      Number of client processes (default: 1)
    --warehouses   Number of warehouses (default: 4)
    --scalefactor  Scale factor (default: 1)
    --stop-on-error  Stop on transaction errors
    --output       Save results as JSON
"""
import sys
import os
import time
import logging
import argparse
import json
from copy import deepcopy
from pprint import pprint

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(funcName)s:%(lineno)03d] %(levelname)-5s: %(message)s",
    datefmt="%m-%d-%Y %H:%M:%S",
    stream=sys.stdout,
)

from tpcc import createDriverClass, startLoading, startExecution
from util import scaleparameters, rand, nurand

DRIVERS = ['baselinemysql', 'deepseekv4flashmysql']


def parse_config(config_path, section):
    from configparser import ConfigParser
    cparser = ConfigParser()

    candidates = [os.path.realpath(config_path)]
    base = os.path.basename(config_path)
    if not config_path.startswith('configs/'):
        candidates.append(os.path.realpath(os.path.join('configs', base)))

    for path in candidates:
        if os.path.exists(path):
            cparser.read(path)
            break

    if cparser.has_section(section):
        return dict(cparser.items(section))
    if cparser.has_section('mysql'):
        return dict(cparser.items('mysql'))
    raise ValueError(
        "No [%s] or [mysql] section found in '%s' (tried: %s)"
        % (section, config_path, candidates)
    )


ANALYTIC_PARAMS = {
    'Q1': [1, 1, 100],
    'Q2': [1],
    'Q3': [100],
}


def run_benchmark(driver_name, config, scale_params, duration, clients, stop_on_error):
    driver_class = createDriverClass(driver_name)
    driver = driver_class("tpcc.sql")

    load_config = deepcopy(config)
    load_config['reset'] = True
    load_config['load'] = False
    load_config['execute'] = False

    driver.loadConfig(load_config)
    logging.info("Loading data for %s..." % driver_name)
    load_start = time.time()
    if clients == 1:
        from runtime import loader
        l = loader.Loader(driver, scale_params,
                          range(scale_params.starting_warehouse, scale_params.ending_warehouse + 1), True)
        driver.loadStart()
        l.execute()
        driver.loadFinish()
    else:
        startLoading(driver_class, scale_params, {'clients': clients, 'ddl': 'tpcc.sql'}, load_config)
    load_time = time.time() - load_start
    logging.info("Data loaded in %.2fs" % load_time)

    exec_config = deepcopy(config)
    exec_config['reset'] = False
    exec_config['execute'] = True

    logging.info("Running benchmark for %s (%d seconds, %d clients, %d warehouses)..." %
                 (driver_name, duration, clients, scale_params.warehouses))
    exec_start = time.time()

    if clients == 1:
        from runtime.executor import Executor
        e = Executor(driver, scale_params, stop_on_error=stop_on_error)
        driver.executeStart()
        tpcc_results = e.execute(duration)
        driver.executeFinish()
    else:
        args = {'clients': clients, 'ddl': 'tpcc.sql', 'duration': duration, 'stop_on_error': stop_on_error}
        tpcc_results = startExecution(driver_class, scale_params, args, exec_config)

    elapsed = time.time() - exec_start

    logging.info("Running 10 analytic queries for %s..." % driver_name)
    analytic_times = {}
    for qname in ['Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6', 'Q7', 'Q8', 'Q9', 'Q10']:
        params = ANALYTIC_PARAMS.get(qname)
        start = time.perf_counter()
        try:
            driver.doAnalyticsQuery(qname, params)
        except Exception as ex:
            logging.warn("Analytic query %s failed: %s" % (qname, ex))
            analytic_times[qname] = None
            continue
        analytic_times[qname] = time.perf_counter() - start

    return tpcc_results, load_time, elapsed, analytic_times


def main():
    parser = argparse.ArgumentParser(description='Compare baselinemysql vs deepseekv4flashmysql TPC-C drivers')
    parser.add_argument('--config', required=True, help='Config file for baselinemysql driver')
    parser.add_argument('--config2', default=None, help='Config file for deepseekv4flashmysql (defaults to --config)')
    parser.add_argument('--duration', default=60, type=int, help='Benchmark duration per run (seconds)')
    parser.add_argument('--clients', default=1, type=int, help='Number of client processes')
    parser.add_argument('--warehouses', default=4, type=int, help='Number of warehouses')
    parser.add_argument('--scalefactor', default=1, type=float, help='Scale factor')
    parser.add_argument('--stop-on-error', action='store_true', help='Stop on transaction errors')
    parser.add_argument('--output', default=None, help='Output JSON file for results')
    args = parser.parse_args()

    config2 = args.config2 or args.config

    scale_params = scaleparameters.makeWithScaleFactor(args.warehouses, args.scalefactor)
    rand.setNURand(nurand.makeForLoad())

    results = {}
    for driver_name in DRIVERS:
        cfg_path = config2 if driver_name == 'deepseekv4flashmysql' else args.config
        config = parse_config(cfg_path, driver_name)
        r, load_t, exec_t, analytic_times = run_benchmark(
            driver_name, config, scale_params,
            args.duration, args.clients, args.stop_on_error
        )
        results[driver_name] = {
            'load_time': load_t,
            'execution_time': exec_t,
            'counters': dict(r.txn_counters),
            'times': dict((k, v) for k, v in r.txn_times.items()),
            'analytic_times': analytic_times,
            'summary': str(r.show())
        }
        print("\n=== %s RESULTS ===" % driver_name.upper())
        print(r.show(load_t))
        print("--- Analytic Query Times ---")
        for qname, t in sorted(analytic_times.items()):
            if t is not None:
                print("  %s: %.2f ms" % (qname, t * 1000))
            else:
                print("  %s: FAILED" % qname)
        print()

    baseline = results[DRIVERS[0]]
    optimized = results[DRIVERS[1]]

    print()
    print("PERFORMANCE COMPARISON")
    print("=" * 70)
    print("%-25s %15s %15s %10s" % ("Metric", "Baseline", "Optimized", "Change"))
    print("-" * 70)

    base_total = sum(baseline['counters'].values())
    opt_total = sum(optimized['counters'].values())
    base_total_time = sum(baseline['times'].values())
    opt_total_time = sum(optimized['times'].values())

    base_tps = base_total / base_total_time if base_total_time > 0 else 0
    opt_tps = opt_total / opt_total_time if opt_total_time > 0 else 0
    if base_tps > 0:
        pct = ((opt_tps - base_tps) / base_tps) * 100
    else:
        pct = 0
    print("%-25s %15.2f %15.2f %+9.1f%%" % ("Throughput (tps)", base_tps, opt_tps, pct))

    base_tpmc = baseline['counters'].get('NEW_ORDER', 0) / (base_total_time / 60) if base_total_time > 0 else 0
    opt_tpmc = optimized['counters'].get('NEW_ORDER', 0) / (opt_total_time / 60) if opt_total_time > 0 else 0
    if base_tpmc > 0:
        pct = ((opt_tpmc - base_tpmc) / base_tpmc) * 100
    else:
        pct = 0
    print("%-25s %15.2f %15.2f %+9.1f%%" % ("tpmC", base_tpmc, opt_tpmc, pct))

    print()
    print("--- Avg Latency per Transaction ---")
    all_txns = set(list(baseline['counters'].keys()) + list(optimized['counters'].keys()))
    for txn in sorted(all_txns):
        base_cnt = baseline['counters'].get(txn, 0)
        opt_cnt = optimized['counters'].get(txn, 0)
        base_t = baseline['times'].get(txn, 0)
        opt_t = optimized['times'].get(txn, 0)
        base_avg_ms = (base_t / base_cnt * 1e3) if base_cnt > 0 else 0
        opt_avg_ms = (opt_t / opt_cnt * 1e3) if opt_cnt > 0 else 0
        if base_avg_ms > 0:
            pct = ((opt_avg_ms - base_avg_ms) / base_avg_ms) * 100
        else:
            pct = 0
        print("  %-23s %13.2f %13.2f %+9.1f%%" % (txn, base_avg_ms, opt_avg_ms, pct))

    print()
    print("--- Analytics Query Latency (ms) ---")
    for qname in sorted(baseline.get('analytic_times', {})):
        bt = baseline['analytic_times'].get(qname)
        ot = optimized['analytic_times'].get(qname)
        if bt is not None and ot is not None:
            pct = ((ot - bt) / bt) * 100 if bt > 0 else 0
            print("  %-23s %13.2f %13.2f %+9.1f%%" % (qname, bt * 1000, ot * 1000, pct))
    print("-" * 70)
    pprint({'baseline_counters': baseline['counters'], 'optimized_counters': optimized['counters']})

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        logging.info("Results saved to %s" % args.output)


if __name__ == '__main__':
    main()
