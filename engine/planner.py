from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StrategyDef:
    name: str
    objective: str
    mechanisms: str
    example_snippet: str


def parse_kb(kb_path: str) -> list[StrategyDef]:
    content = Path(kb_path).read_text()
    strategies: list[StrategyDef] = []
    blocks = re.split(r"\n##\s+", content)

    for block in blocks:
        if not block.strip() or block.startswith("# "):
            continue
        name_match = re.match(r"^(\d+\.\s*)?(.+)", block)
        if not name_match:
            continue
        name = name_match.group(2).strip()

        obj_match = re.search(r"\*   \*\*Objective\*\*:\s*(.+)", block)
        mech_match = re.search(r"\*   \*\*Mechanisms?\*\*:\s*((?:.|\n)*?)(?=\n\*|\Z)", block)
        example_match = re.search(r"```python\n(.+?)```", block, re.DOTALL)

        strategies.append(
            StrategyDef(
                name=name,
                objective=obj_match.group(1).strip() if obj_match else "",
                mechanisms=mech_match.group(1).strip() if mech_match else "",
                example_snippet=example_match.group(1).strip() if example_match else "",
            )
        )
    return strategies
