#!/usr/bin/env python
"""
Record-and-replay transaction correctness check.

Loads identical data into both databases (same RNG seed), runs N
transactions through baselinemysql recording every (txn, params, result),
then replays the same params through deepseekv4flashmysql and compares.

Usage:
    uv run python scripts/correctness_check.py \\
        --config=configs/baselinemysql.config \\
        --config2=configs/deepseekv4flashmysql.config \\
        --warehouses=4 --transactions=500
"""
import sys
import os
# Add the project root to sys.path so that absolute imports of tpcc package work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

TPCC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DDL_PATH = os.path.join(TPCC_DIR, "tpcc.sql")

import logging
import argparse
import random as rng
from datetime import datetime
from copy import deepcopy

from tpcc.util import nurand, rand

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(funcName)s:%(lineno)03d] %(levelname)-5s: %(message)s",
    datefmt="%m-%d-%Y %H:%M:%S",
    stream=sys.stdout,
)

from tpcc import createDriverClass, startLoading
from tpcc.util import scaleparameters

DRIVERS = ['baselinemysql', 'deepseekv4flashmysql', 'deepseekv4flashmysqlv2']


def parse_config(config_path, section):
    from configparser import ConfigParser
    cparser = ConfigParser()

    script_dir = os.path.dirname(os.path.realpath(__file__))
    tpcc_dir = os.path.dirname(script_dir)
    project_root = os.path.dirname(tpcc_dir)
    base = os.path.basename(config_path)

    candidates = [
        os.path.realpath(config_path),
        os.path.realpath(os.path.join(project_root, config_path)),
        os.path.realpath(os.path.join(tpcc_dir, config_path)),
        os.path.realpath(os.path.join(tpcc_dir, 'configs', base)),
        os.path.realpath(os.path.join(project_root, 'tpcc', config_path)),
    ]

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


def load_database(driver_name, config, scale_params):
    driver_class = createDriverClass(driver_name)
    driver = driver_class(DDL_PATH)

    cfg = deepcopy(config)
    cfg['reset'] = True
    cfg['load'] = False
    cfg['execute'] = False

    driver.loadConfig(cfg)
    logging.info("Loading data for %s..." % driver_name)

    from tpcc.runtime import loader
    l = loader.Loader(driver, scale_params,
                      range(scale_params.starting_warehouse, scale_params.ending_warehouse + 1), True)
    driver.loadStart()
    l.execute()
    driver.loadFinish()

    driver.conn.commit()
    logging.info("Data loaded for %s" % driver_name)
    return driver


def connect_driver(driver_name, config):
    import MySQLdb as mysql
    driver_class = createDriverClass(driver_name)
    driver = driver_class(DDL_PATH)
    driver.host = str(config["host"])
    driver.port = int(config["port"])
    driver.user = str(config["user"])
    driver.password = str(config["password"])
    driver.database = str(config["database"])
    driver.conn = mysql.connect(
        host=driver.host, port=driver.port,
        user=driver.user, password=driver.password,
        database=driver.database, charset='utf8',
    )
    driver.cursor = driver.conn.cursor()
    return driver


def compare_values(a, b, path=""):
    mismatches = []
    if type(a) != type(b):
        return [(path, a, b, "type mismatch: %s vs %s" % (type(a).__name__, type(b).__name__))]

    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return [(path, a, b, "length %d vs %d" % (len(a), len(b)))]
        for i, (x, y) in enumerate(zip(a, b)):
            mismatches.extend(compare_values(x, y, "%s[%d]" % (path, i)))
    elif isinstance(a, dict):
        ka, kb = set(a.keys()), set(b.keys())
        if ka != kb:
            return [(path, a, b, "keys differ: %s vs %s" % (ka - kb, kb - ka))]
        for k in a:
            mismatches.extend(compare_values(a[k], b[k], "%s.%s" % (path, k)))
    elif isinstance(a, float):
        if abs(a - b) > 0.001:
            mismatches.append((path, a, b, "float diff %f" % abs(a - b)))
    elif isinstance(a, int):
        if a != b:
            mismatches.append((path, a, b, "int diff %d" % (a - b)))
    elif isinstance(a, str):
        if a != b:
            mismatches.append((path, a, b, "str diff"))
    elif isinstance(a, bytes):
        if a != b:
            mismatches.append((path, a, b, "bytes diff"))
    elif isinstance(a, datetime):
        pass  # timestamps differ between record/replay, ignore
    elif a is None and b is None:
        pass
    else:
        if a != b:
            mismatches.append((path, a, b, "diff"))
    return mismatches


def main():
    parser = argparse.ArgumentParser(
        description='Record-and-replay transaction correctness check')
    parser.add_argument('--config', required=True, help='Configuration file')
    parser.add_argument('--config2', default=None, help='Config for deepseek (defaults to --config)')
    parser.add_argument('--config3', default=None, help='Config for deepseekv2 (defaults to --config)')
    parser.add_argument('--warehouses', default=4, type=int, help='Number of warehouses')
    parser.add_argument('--scalefactor', default=1, type=float, help='Scale factor')
    parser.add_argument('--transactions', default=500, type=int, help='Number of transactions to run')
    parser.add_argument('--stop-on-error', action='store_true', help='Stop on first mismatch')
    args = parser.parse_args()

    config2 = args.config2 or args.config
    config3 = args.config3 or args.config
    scale_params = scaleparameters.makeWithScaleFactor(args.warehouses, args.scalefactor)
    rand.setNURand(nurand.makeForLoad())

    # Load data into all databases (same RNG state for identical data)
    baseline_config = parse_config(args.config, DRIVERS[0])
    deepseek_config = parse_config(config2, DRIVERS[1])
    deepseekv2_config = parse_config(config3, DRIVERS[2])

    rng_state = rng.getstate()

    baseline = load_database(DRIVERS[0], baseline_config, scale_params)
    baseline.conn.close()

    rng.setstate(rng_state)
    deepseek = load_database(DRIVERS[1], deepseek_config, scale_params)
    deepseek.conn.close()

    rng.setstate(rng_state)
    deepseekv2 = load_database(DRIVERS[2], deepseekv2_config, scale_params)
    deepseekv2.conn.close()

    # Reconnect for execution
    baseline = connect_driver(DRIVERS[0], baseline_config)
    deepseek = connect_driver(DRIVERS[1], deepseek_config)
    deepseekv2 = connect_driver(DRIVERS[2], deepseekv2_config)

    # Record phase: run N transactions through baseline
    logging.info("Recording %d transactions from baseline..." % args.transactions)
    recorded = []
    for i in range(args.transactions):
        from tpcc.runtime.executor import Executor
        executor = Executor(baseline, scale_params)
        txn, params = executor.doOne()
        result = baseline.executeTransaction(txn, params)
        recorded.append((txn, params, result))
        if (i + 1) % 100 == 0:
            baseline.conn.commit()
    baseline.conn.commit()
    logging.info("Recorded %d transactions" % len(recorded))

    # Replay phase: replay same params through deepseek
    logging.info("Replaying %d transactions through deepseek..." % len(recorded))
    total = len(recorded)
    passed = 0
    failed = 0

    for i, (txn, params, expected) in enumerate(recorded):
        actual = deepseek.executeTransaction(txn, params)
        mismatches = compare_values(expected, actual)
        if mismatches:
            failed += 1
            logging.error("MISMATCH #%d: %s" % (i + 1, txn))
            for path, exp, act, reason in mismatches[:5]:
                logging.error("  %s: expected=%r actual=%r (%s)" % (path, exp, act, reason))
            if args.stop_on_error:
                sys.exit(1)
        else:
            passed += 1
        if (i + 1) % 100 == 0:
            deepseek.conn.commit()
    deepseek.conn.commit()

    v1_passed, v1_total = passed, total

    # Replay phase 2: replay same params through deepseekv2
    logging.info("Replaying %d transactions through deepseekv2..." % len(recorded))
    passed = 0
    failed = 0

    for i, (txn, params, expected) in enumerate(recorded):
        actual = deepseekv2.executeTransaction(txn, params)
        mismatches = compare_values(expected, actual)
        if mismatches:
            failed += 1
            logging.error("MISMATCH (v2) #%d: %s" % (i + 1, txn))
            for path, exp, act, reason in mismatches[:5]:
                logging.error("  %s: expected=%r actual=%r (%s)" % (path, exp, act, reason))
            if args.stop_on_error:
                sys.exit(1)
        else:
            passed += 1
        if (i + 1) % 100 == 0:
            deepseekv2.conn.commit()
    deepseekv2.conn.commit()

    v2_passed, v2_total = passed, total

    print("=" * 60)
    print("deepseekv4flashmysql:  %d/%d passed, %d failed" % (v1_passed, v1_total, v1_total - v1_passed))
    print("deepseekv4flashmysqlv2: %d/%d passed, %d failed" % (v2_passed, v2_total, v2_total - v2_passed))
    if v1_failed := (v1_total - v1_passed):
        print("deepseekv4flashmysql: SOME TRANSACTIONS MISMATCH")
    if v2_f := (v2_total - v2_passed):
        print("deepseekv4flashmysqlv2: SOME TRANSACTIONS MISMATCH")
    if (v1_total - v1_passed) == 0 and (v2_total - v2_passed) == 0:
        print("ALL DRIVERS MATCH BASELINE")
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
