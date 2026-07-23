"""
SQLite-backed telemetry for ADCo engine and checker runs.

Engine: engine_runs (summary) + engine_steps (per-step details).
Checker: checker_runs (one row per run).

Usage:
    from telemetry import TelemetryRun

    # Engine
    with TelemetryRun(run_type="engine", model_name="gemini-3.5-flash") as run:
        run.record_step("scanner", duration_ms=120)
        run.record_step("intent_extractor", duration_ms=3000, usage_metadata=...)
        run.record_step("code_generator", duration_ms=5000, usage_metadata=...)
        run.record_step("verifier", duration_ms=10)

    # Checker
    with TelemetryRun(run_type="checker", model_name="gemini-3.5-flash") as run:
        run.record_check(status="PASS", reason="", usage_metadata=...)
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import time
import uuid
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "telemetry", "telemetry.db")


def _open_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = _open_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS engine_runs (
            id              TEXT PRIMARY KEY,
            timestamp       TEXT NOT NULL,
            model           TEXT,
            run_status      TEXT CHECK(run_status IN ('running', 'success', 'fail')),
            total_duration_ms INTEGER DEFAULT 0,
            total_input_tokens  INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS engine_steps (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          TEXT NOT NULL REFERENCES engine_runs(id),
            timestamp       TEXT NOT NULL,
            step            TEXT NOT NULL,
            step_duration_ms INTEGER,
            llm_input_tokens  INTEGER DEFAULT 0,
            llm_output_tokens INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS checker_runs (
            id              TEXT PRIMARY KEY,
            engine_run_id   TEXT,
            timestamp       TEXT NOT NULL,
            model           TEXT,
            run_status      TEXT CHECK(run_status IN ('running', 'success', 'fail')),
            checker_status  TEXT,
            reason          TEXT,
            total_duration_ms INTEGER DEFAULT 0,
            llm_input_tokens  INTEGER DEFAULT 0,
            llm_output_tokens INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tpcc_runs (
            id              TEXT PRIMARY KEY,
            engine_run_id   TEXT,
            timestamp       TEXT NOT NULL,
            driver          TEXT,
            benchmark_duration_s INTEGER,
            run_status      TEXT CHECK(run_status IN ('running', 'success', 'fail')),
            duration_ms     INTEGER DEFAULT 0,
            exit_code       INTEGER,
            total_executed  INTEGER DEFAULT 0,
            total_time_us   REAL DEFAULT 0,
            total_tps       REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tpcc_txns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tpcc_run_id     TEXT NOT NULL REFERENCES tpcc_runs(id),
            txn_type        TEXT NOT NULL,
            status          TEXT CHECK(status IN ('success', 'fail')),
            executed        INTEGER DEFAULT 0,
            time_us         REAL DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()


class TelemetryRun:
    """Records engine steps or checker results into SQLite."""

    def __init__(self, run_type: str, model_name: str = "", engine_run_id: str = "") -> None:
        self.run_id = str(uuid.uuid4())
        self.run_type = run_type
        self.model_name = model_name
        self.engine_run_id = engine_run_id
        self.started_at = time.time()
        self._conn: Optional[sqlite3.Connection] = None
        self._run_created: bool = False

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _open_db()
        return self._conn

    def _now_iso(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _ensure_run(self) -> None:
        if not self._run_created:
            conn = self._get_conn()
            if self.run_type == "engine":
                conn.execute(
                    "INSERT INTO engine_runs (id, timestamp, model, run_status) VALUES (?, ?, ?, ?)",
                    (self.run_id, self._now_iso(), self.model_name, "running"),
                )
            elif self.run_type == "checker":
                conn.execute(
                    "INSERT INTO checker_runs (id, engine_run_id, timestamp, model, run_status) VALUES (?, ?, ?, ?, ?)",
                    (self.run_id, self.engine_run_id or None, self._now_iso(), self.model_name, "running"),
                )
            # tpcc: no separate run row — record_tpcc handles the insert
            conn.commit()
            self._run_created = True

    def __enter__(self) -> "TelemetryRun":
        init_db()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:  # type: ignore
        status = "fail" if exc_type is not None else "success"
        total_ms = int((time.time() - self.started_at) * 1000)
        conn = self._get_conn()

        if self.run_type == "engine":
            if self._run_created:
                total_input, total_output = _sum_step_tokens(conn, self.run_id)
                conn.execute(
                    "UPDATE engine_runs SET run_status = ?, total_duration_ms = ?, total_input_tokens = ?, total_output_tokens = ? WHERE id = ?",
                    (status, total_ms, total_input, total_output, self.run_id),
                )
            else:
                conn.execute(
                    "INSERT INTO engine_runs (id, timestamp, model, run_status, total_duration_ms, total_input_tokens, total_output_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (self.run_id, self._now_iso(), self.model_name, status, total_ms, 0, 0),
                )
        elif self.run_type == "checker" and self._run_created:
            conn.execute(
                "UPDATE checker_runs SET run_status = ?, total_duration_ms = ? WHERE id = ?",
                (status, total_ms, self.run_id),
            )
        # tpcc: handled entirely by record_tpcc

        conn.commit()
        conn.close()
        return False

    # ── Engine API ──

    def record_step(
        self,
        step: str,
        duration_ms: int,
        usage_metadata=None,
    ) -> None:
        self._ensure_run()
        input_tokens, output_tokens = _extract_tokens(usage_metadata)
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO engine_steps
               (run_id, timestamp, step, step_duration_ms,
                llm_input_tokens, llm_output_tokens)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (self.run_id, self._now_iso(), step, duration_ms, input_tokens, output_tokens),
        )
        conn.commit()

    # ── Checker API ──

    def record_check(
        self,
        status: str = "",
        reason: str = "",
        usage_metadata=None,
    ) -> None:
        self._ensure_run()
        input_tokens, output_tokens = _extract_tokens(usage_metadata)
        conn = self._get_conn()
        conn.execute(
            """UPDATE checker_runs SET checker_status = ?, reason = ?,
               llm_input_tokens = ?, llm_output_tokens = ?
               WHERE id = ?""",
            (status, reason, input_tokens, output_tokens, self.run_id),
        )
        conn.commit()

    # ── TPC-C API ──

    def record_tpcc(
        self,
        driver: str = "",
        benchmark_duration_s: int = 0,
        duration_ms: int = 0,
        exit_code: int = 0,
        total_executed: int = 0,
        total_time_us: float = 0,
        total_tps: float = 0,
        txns: list[dict] | None = None,
    ) -> None:
        self._ensure_run()
        run_status = "success" if exit_code == 0 else "fail"
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO tpcc_runs
               (id, engine_run_id, timestamp, driver, benchmark_duration_s, run_status,
                duration_ms, exit_code, total_executed, total_time_us, total_tps)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.run_id,
                self.engine_run_id or None,
                self._now_iso(),
                driver,
                benchmark_duration_s,
                run_status,
                duration_ms,
                exit_code,
                total_executed,
                total_time_us,
                total_tps,
            ),
        )
        for txn in (txns or []):
            conn.execute(
                """INSERT INTO tpcc_txns
                   (tpcc_run_id, txn_type, status, executed, time_us)
                   VALUES (?, ?, ?, ?, ?)""",
                (self.run_id, txn["txn_type"], txn["status"], txn["executed"], txn["time_us"]),
            )
        conn.commit()

# ── Helpers ──

def _extract_tokens(usage_metadata) -> tuple[int, int]:
    if usage_metadata is None:
        return 0, 0

    input_fields = ("prompt_token_count", "promptTokenCount", "input_tokens")
    output_fields = (
        "response_token_count", "responseTokenCount",
        "candidates_token_count", "candidatesTokenCount",
        "output_tokens",
    )

    input_tokens = 0
    for fld in input_fields:
        val = getattr(usage_metadata, fld, None)
        if val is not None:
            input_tokens = int(val)
            break

    output_tokens = 0
    for fld in output_fields:
        val = getattr(usage_metadata, fld, None)
        if val is not None:
            output_tokens = int(val)
            break

    return input_tokens, output_tokens


def _sum_step_tokens(conn: sqlite3.Connection, run_id: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT COALESCE(SUM(llm_input_tokens), 0), COALESCE(SUM(llm_output_tokens), 0) FROM engine_steps WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return row if row else (0, 0)
