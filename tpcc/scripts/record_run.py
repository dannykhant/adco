#!/usr/bin/env python
"""
Run a TPC-C benchmark and record results to telemetry.

Extracts ADCO_RUN_ID from the driver file and links the benchmark run.
If no run_id is found, runs tpcc without telemetry (not a generated driver).

Output is parsed into structured tpcc_runs and tpcc_txns tables.

Usage:
    uv run python tpcc/scripts/record_run.py <driver_name> [tpcc args...]
    uv run python tpcc/scripts/record_run.py optimizedmysql --config=...
"""

import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ADCO_RUN_ID_RE = re.compile(r"# ADCO_RUN_ID: ([a-f0-9-]+)")
DURATION_RE = re.compile(r"Execution Results after (\d+) seconds")

ALL_TXN_TYPES = {"DELIVERY", "NEW_ORDER", "ORDER_STATUS", "PAYMENT", "STOCK_LEVEL"}


def _find_driver_file(driver_name: str) -> str | None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    drivers_dir = os.path.join(os.path.dirname(script_dir), "drivers")
    driver_file = os.path.join(drivers_dir, f"{driver_name}driver.py")
    if os.path.isfile(driver_file):
        return driver_file
    return None


def _extract_run_id(driver_name: str) -> str:
    path = _find_driver_file(driver_name)
    if path is None:
        return ""
    with open(path) as f:
        m = ADCO_RUN_ID_RE.search(f.read())
    return m.group(1) if m else ""


def _parse_output(stdout: str) -> dict:
    """Parse tpcc.py stdout into structured metrics.

    The output uses 16-char fixed-width columns. Long float values can overflow
    their column, merging with the rate column. We extract the rate from the
    rightmost column (which always contains "NUM txn/s"), and the time_us from
    the space between column 34 and wherever the rate starts.
    """
    result: dict = {
        "benchmark_duration_s": 0,
        "total_executed": 0,
        "total_time_us": 0.0,
        "total_tps": 0.0,
        "txns": [],
    }

    dur_match = DURATION_RE.search(stdout)
    if dur_match:
        result["benchmark_duration_s"] = int(dur_match.group(1))

    seen_txns: set[str] = set()
    for line in stdout.splitlines():
        if "txn/s" not in line:
            continue

        # First two columns are fixed 16-char (plus 2 leading spaces)
        txn_name = line[2:18].strip()
        if not txn_name or txn_name == "Executed":
            continue
        executed_str = line[18:34].strip()
        if not executed_str.isdigit():
            continue
        executed = int(executed_str)

        # Rate column is "%.02f txn/s" in the rightmost 16 chars.
        # Do NOT rstrip — padding is what lets us use fixed offsets.
        rate_col = line[-16:]
        rate_str = rate_col.split("txn/s")[0].strip()
        rate = float(rate_str) if rate_str else 0.0

        # Time_us is everything between col 34 and the rate column
        rate_start = len(line) - 16
        time_us_str = line[34:rate_start].strip().rstrip(".")
        time_us = float(time_us_str) if time_us_str else 0.0

        if txn_name == "TOTAL":
            result["total_executed"] = executed
            result["total_time_us"] = time_us
            # Ignore parsed rate — compute from benchmark duration directly
        else:
            result["txns"].append({
                "txn_type": txn_name,
                "status": "success",
                "executed": executed,
                "time_us": time_us,
            })
            seen_txns.add(txn_name)

    for txn_type in sorted(ALL_TXN_TYPES - seen_txns):
        result["txns"].append({
            "txn_type": txn_type,
            "status": "fail",
            "executed": 0,
            "time_us": 0.0,
        })

    # total_tps = total_executed / benchmark_duration_s
    if result["benchmark_duration_s"] and result["total_executed"]:
        result["total_tps"] = result["total_executed"] / result["benchmark_duration_s"]

    return result


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: record_run.py <driver_name> [tpcc args...]", file=sys.stderr)
        sys.exit(1)

    driver_name = sys.argv[1]
    run_id = _extract_run_id(driver_name)

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    tpcc_path = os.path.join(root, "tpcc", "tpcc.py")
    tpcc_args = [sys.executable, tpcc_path] + sys.argv[1:]

    if not run_id:
        print("[telemetry] No ADCO_RUN_ID — not a generated driver. Skipping telemetry.")
        result = subprocess.run(tpcc_args)
        sys.exit(result.returncode)

    from telemetry import TelemetryRun

    print(f"[telemetry] Run ID: {run_id}")
    t0 = time.time()
    result = subprocess.run(tpcc_args, capture_output=True, text=True)
    duration_ms = int((time.time() - t0) * 1000)

    parsed = _parse_output(result.stdout)

    with TelemetryRun(run_type="tpcc", engine_run_id=run_id) as run:
        run.record_tpcc(
            driver=driver_name,
            benchmark_duration_s=parsed["benchmark_duration_s"],
            duration_ms=duration_ms,
            exit_code=result.returncode,
            total_executed=parsed["total_executed"],
            total_time_us=parsed["total_time_us"],
            total_tps=parsed["total_tps"],
            txns=parsed["txns"],
        )

    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
