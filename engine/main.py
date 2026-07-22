"""
Application-Database Co-design (ADCo) — Query Rewrite Engine

Optimize a database-interaction file by providing the target file
and any supporting files for context.

Usage:
    uv run python -m engine.main <target_file>
    uv run python -m engine.main <target_file> --with <support> [--with <support> ...]
    uv run python -m engine.main <target_file> --dry-run
"""

import argparse
import os

from dotenv import load_dotenv

from .pipeline import Pipeline

load_dotenv()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_PATH = os.path.join(ROOT_DIR, "docs", "kb", "query_rewrite_methods.md")


def main():
    parser = argparse.ArgumentParser(
        description="ADCo — Application-Database Co-optimization Engine"
    )
    parser.add_argument("target",
                        help="The file to optimize")
    parser.add_argument("--runner", "-r", required=True,
                        help="Main runner/entry point file (injected into both extraction and generation prompts)")
    parser.add_argument("--with", "-w", dest="support", action="append", default=[],
                        help="Supporting file(s) providing context (repeatable)")
    parser.add_argument("--model", default="gemini-2.5-flash",
                        help="Gemini model ID (default: gemini-2.5-flash)")
    parser.add_argument("--kb", default=None,
                        help="Path to knowledge base of rewrite strategies")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (defaults to target file's directory)")
    parser.add_argument("--llm-delay", type=int, default=5,
                        help="Seconds to wait between LLM calls (default: 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompts without calling LLM")
    args = parser.parse_args()

    pipe = Pipeline(
        kb_path=args.kb or KB_PATH,
        output_dir=args.output_dir,
        model_name=args.model,
        llm_delay=args.llm_delay,
    )

    pipe.run(
        target_path=args.target,
        runner_path=args.runner,
        support_files=args.support,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
