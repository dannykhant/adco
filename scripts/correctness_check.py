#!/usr/bin/env python
"""
TPC-C Analytic Query Correctness: baselinemysql vs deepseekv4flashmysql

Loads data once, runs each analytic query (Q1–Q10) through both drivers
against the same database, and compares outputs.

Logs all queries and results to stdout for developer inspection.
"""
import sys
import os
import logging
import argparse
from copy import deepcopy
from pprint import pformat

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(funcName)s:%(lineno)03d] %(levelname)-5s: %(message)s",
    datefmt="%m-%d-%Y %H:%M:%S",
    stream=sys.stdout,
)

from tpcc import createDriverClass
from util import scaleparameters, rand, nurand
from drivers.baselinemysqldriver import ANALYTIC_QUERIES as BASELINE_QUERIES
from drivers.deepseekv4flashmysqldriver import ANALYTIC_QUERIES as DEEPSEEK_QUERIES

try:
    import MySQLdb as mysql
except ImportError:
    import pymysql as mysql

DRIVERS = ['baselinemysql', 'deepseekv4flashmysql']

ANALYTIC_PARAMS = {
    'Q1': [1, 1, 100],
    'Q2': [1],
    'Q3': [100],
}


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
        "No [%s] or [mysql] section found" % section
    )


def connect_driver(name, config):
    d = createDriverClass(name)('tpcc.sql')
    d.host = str(config["host"])
    d.port = int(config["port"])
    d.user = str(config["user"])
    d.password = str(config["password"])
    d.database = str(config["database"])
    d.conn = mysql.connect(
        host=d.host,
        port=d.port,
        user=d.user,
        password=d.password,
        database=d.database,
        charset='utf8',
    )
    d.cursor = d.conn.cursor()
    return d


def normalize(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return tuple(normalize(x) for x in v)
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, dict):
        return {k: normalize(vk) for k, vk in v.items()}
    return v


ANALYTIC_QUERIES = {
    'baselinemysql': BASELINE_QUERIES,
    'deepseekv4flashmysql': DEEPSEEK_QUERIES,
}


def main():
    parser = argparse.ArgumentParser(
        description='Check analytic query correctness: baselinemysql vs deepseekv4flashmysql')
    parser.add_argument('--config', required=True, help='Configuration file')
    parser.add_argument('--warehouses', default=1, type=int, help='Number of warehouses')
    parser.add_argument('--scalefactor', default=1, type=float, help='Scale factor')
    args = parser.parse_args()

    scale_params = scaleparameters.makeWithScaleFactor(args.warehouses, args.scalefactor)
    rand.setNURand(nurand.makeForLoad())

    config = parse_config(args.config, DRIVERS[0])
    config['reset'] = True

    print("Loading data via %s..." % DRIVERS[0])
    loader_driver = createDriverClass(DRIVERS[0])('tpcc.sql')
    loader_driver.loadConfig(deepcopy(config))

    from runtime import loader
    l = loader.Loader(loader_driver, scale_params,
                      range(scale_params.starting_warehouse, scale_params.ending_warehouse + 1), True)
    loader_driver.loadStart()
    l.execute()
    loader_driver.loadFinish()
    loader_driver.conn.close()

    drivers = {}
    for name in DRIVERS:
        drivers[name] = connect_driver(name, config)

    print("\nRunning analytic queries...")
    print("=" * 80)
    all_ok = True
    total = 0
    passed = 0

    for qname in ['Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6', 'Q7', 'Q8', 'Q9', 'Q10']:
        params = ANALYTIC_PARAMS.get(qname)
        total += 1

        print("\n" + "-" * 80)
        for name in DRIVERS:
            sql = ANALYTIC_QUERIES[name][qname]
            print("[%s] %s SQL:" % (name, qname))
            for line in sql.strip().split('\n'):
                print("  " + line)
            if params:
                print("  -- params: %s" % params)

        results = {}
        errors = {}
        for name in DRIVERS:
            try:
                rows = drivers[name].doAnalyticsQuery(qname, params)
                results[name] = normalize(rows)
                errors[name] = None
            except Exception as ex:
                errors[name] = ex
                results[name] = None

        for name in DRIVERS:
            r = results[name]
            if errors[name]:
                print("[%s] ERROR: %s" % (name, errors[name]))
            else:
                print("[%s] %s rows returned:" % (name, len(r) if isinstance(r, tuple) else 1))
                print("  " + pformat(r)[:2000])

        if results[DRIVERS[0]] == results[DRIVERS[1]]:
            passed += 1
            print(">>> %s: PASS" % qname)
        else:
            all_ok = False
            print(">>> %s: FAIL (results differ)" % qname)

    print("=" * 80)
    print("\nResult: %d/%d correct" % (passed, total))
    if all_ok:
        print("=== ALL QUERIES MATCH ===")
    else:
        print("=== SOME QUERIES MISMATCH ===")
        sys.exit(1)


if __name__ == '__main__':
    main()
