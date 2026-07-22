from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class VerificationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: "VerificationResult") -> "VerificationResult":
        return VerificationResult(
            passed=self.passed and other.passed,
            errors=self.errors + other.errors,
            warnings=self.warnings + other.warnings,
        )


def _check_compile(code: str, filename: str = "<generated>") -> VerificationResult:
    try:
        compile(code, filename, "exec")
        return VerificationResult(passed=True)
    except SyntaxError as e:
        return VerificationResult(
            passed=False,
            errors=[f"Syntax error at line {e.lineno}: {e.msg}"],
        )
    except Exception as e:
        return VerificationResult(
            passed=False,
            errors=[f"Compilation error: {e}"],
        )


def _check_not_empty(code: str) -> VerificationResult:
    if not code.strip():
        return VerificationResult(
            passed=False,
            errors=["Generated code is empty"],
        )
    return VerificationResult(passed=True)


def _check_no_window_functions(code: str) -> VerificationResult:
    import re
    bad = re.compile(r'\b(ROW_NUMBER|RANK|DENSE_RANK)\s*\(|OVER\s*\(|PARTITION\s+BY\b', re.IGNORECASE)
    errors = []
    for m in bad.finditer(code):
        line_num = code[:m.start()].count('\n') + 1
        errors.append(f"MySQL 5.7 incompatible at line {line_num}: '{m.group()}'")
    return VerificationResult(passed=not errors, errors=errors)


def _check_no_prefixed_vars(code: str) -> VerificationResult:
    import re
    errors = []
    for match in re.finditer(r'\b(w_w_id|d_d_id|c_c_id)\b', code):
        line_num = code[:match.start()].count('\n') + 1
        errors.append(f"Undefined variable '{match.group()}' at line {line_num}; use exact param key instead")
    return VerificationResult(passed=not errors, errors=errors)


VERIFIER_CHECKS: list[Callable[[str], VerificationResult]] = [
    _check_not_empty,
    _check_compile,
    _check_no_window_functions,
    _check_no_prefixed_vars,
]


def verify_code(
    code: str,
    filename: str = "<generated>",
    extra_checks: Optional[list[Callable[[str], VerificationResult]]] = None,
) -> VerificationResult:
    checks = list(VERIFIER_CHECKS)
    if extra_checks:
        checks.extend(extra_checks)

    result = VerificationResult(passed=True)
    for check in checks:
        r = check(code)
        result = result.merge(r)
        if not result.passed:
            return result

    return result


def format_result(result: VerificationResult) -> str:
    if result.passed:
        parts = ["  Verifier: PASS"]
    else:
        parts = ["  Verifier: FAIL"]
    for err in result.errors:
        parts.append(f"    {err}")
    for warn in result.warnings:
        parts.append(f"    (warn) {warn}")
    return "\n".join(parts)
