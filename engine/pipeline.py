from __future__ import annotations

import os
import time

from .scanner import scan_project
from .extractor import extract_intent
from .generator import build_optimization_prompt, generate_optimizations
from .planner import parse_kb
from .verifier import verify_code, format_result


class Pipeline:
    def __init__(
        self,
        kb_path: str,
        output_dir: str | None = None,
        model_name: str = "gemini-2.5-flash",
        llm_delay: int = 5,
    ):
        self.kb_path = kb_path
        self.output_dir = output_dir
        self.model_name = model_name
        self.strategies = parse_kb(kb_path)
        self.llm_delay = llm_delay

    def run(
        self,
        target_path: str,
        runner_path: str,
        support_files: list[str] | None = None,
        dry_run: bool = False,
    ) -> str | None:
        target_path = os.path.abspath(target_path)
        runner_path = os.path.abspath(runner_path)
        support_files = support_files or []

        if not os.path.isfile(target_path):
            print(f"  ERROR: Target file not found: {target_path}")
            return None
        if not os.path.isfile(runner_path):
            print(f"  ERROR: Runner file not found: {runner_path}")
            return None

        print(f"  Target:   {target_path}")
        print(f"  Runner:   {runner_path}")
        if support_files:
            print(f"  Support:  {len(support_files)} file(s)")
        print(f"  Model:    {self.model_name}")

        from google import genai
        client = genai.Client()

        # ── Step 1: Build project tree for context ──
        project_root = os.path.dirname(target_path)
        print("  [1/4] Scanning project structure...", end=" ", flush=True)
        tree, _ = scan_project(project_root)
        print("done")

        # ── Step 2: Read provided files ──
        print("  [2/4] Reading files...", end=" ", flush=True)

        runner_content = _read_or_die(runner_path)
        if runner_content is None:
            return None

        all_file_contents = {runner_path: runner_content}
        for p in [target_path] + [os.path.abspath(p) for p in support_files]:
            content = _read_or_die(p)
            if content is None:
                return None
            all_file_contents[p] = content

        print(f"done — {len(all_file_contents)} file(s) loaded")

        target_content = all_file_contents.get(target_path, "")
        support_contents = {p: c for p, c in all_file_contents.items() if p != target_path and p != runner_path}

        if not dry_run and self.llm_delay > 0:
            time.sleep(self.llm_delay)

        # ── Step 3: LLM Intent Extraction ──
        print("  [3/4] Extracting intent...", end=" ", flush=True)
        intent = extract_intent(
            tree=tree,
            runner_content=runner_content,
            file_contents=all_file_contents,
            client=client,
            model_name=self.model_name,
            runner_path=runner_path,
            target_path=target_path,
            dry_run=dry_run,
        )
        print(f"done — {len(intent.transactions)} transactions, db={intent.db_type}")

        if not dry_run and not intent.transactions and intent.db_type == "unknown":
            print(f"  ERROR: Intent extraction failed — no transactions or database identified.")
            if "unparseable" in intent.summary.lower():
                print(f"  Raw response: {intent.summary[-200:]}")
            return None

        if self.llm_delay > 0:
            time.sleep(self.llm_delay)

        # ── Determine output path ──
        target_dir = os.path.dirname(target_path)
        base_dir = os.path.abspath(self.output_dir) if self.output_dir else target_dir

        if intent.output_target:
            if os.path.isdir(intent.output_target):
                output_path = os.path.join(intent.output_target, "optimized.py")
            else:
                output_path = intent.output_target
        else:
            output_path = os.path.join(base_dir, "optimized.py")

        # ── Step 4: LLM Code Generation ──
        print("  [4/4] Generating optimized code...", end=" ", flush=True)
        prompt = build_optimization_prompt(
            tree=tree,
            runner_content=runner_content,
            target_content=target_content,
            support_contents=support_contents,
            intent=intent,
            strategies=self.strategies,
            output_path=output_path,
        )

        if dry_run:
            print("=== GENERATION PROMPT ===")
            print(prompt)
            print("=== END GENERATION PROMPT ===")
            return None

        code = generate_optimizations(
            prompt=prompt,
            output_path=output_path,
            model_name=self.model_name,
            client=client,
        )

        print(f"done — {len(code)} bytes -> {output_path}")

        result = verify_code(code, filename=output_path)
        print(format_result(result))

        if not result.passed:
            for err in result.errors:
                print(f"  WARNING — {err}")
            return None

        print("  All checks passed.")
        return code


def _read_or_die(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except Exception as e:
        print(f"\n  ERROR reading {path}: {e}")
        return None
