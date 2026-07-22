from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class QueryIntent:
    name: str
    sql_template: str
    params: list[str]
    purpose: str
    file_path: str
    line_number: int


@dataclass
class TransactionIntent:
    name: str
    description: str
    queries: list[QueryIntent] = field(default_factory=list)
    dataflow_summary: str = ""
    method_signature: str = ""
    file_path: str = ""
    line_number: int = 0

    @property
    def round_trips(self) -> int:
        return len(self.queries)


@dataclass
class IntentSpec:
    summary: str
    db_type: str
    db_api: str
    db_version: str = ""
    target_file: str = ""
    output_target: str = ""
    plan_summary: str = ""
    support_summary: str = ""
    support_summaries: list[dict] = field(default_factory=list)
    runner_summary: str = ""
    transactions: list[TransactionIntent] = field(default_factory=list)
    conventions: dict = field(default_factory=dict)

    @property
    def total_queries(self) -> int:
        return sum(len(t.queries) for t in self.transactions)

    @property
    def total_round_trips(self) -> int:
        return sum(t.round_trips for t in self.transactions)
