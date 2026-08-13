"""Structured package, runtime and candidate-judge checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from app_paths import data_dir
from release_manifest import CheckReport, run_package_check, run_runtime_check


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def run_package_self_check(report_path: Path | str | None = None) -> CheckReport:
    report = run_package_check(app_dir())
    target = (
        Path(report_path)
        if report_path is not None
        else data_dir() / "diagnostics" / "package-check.json"
    )
    report.write(target)
    return report


def run_self_check(
    report_path: Path | str | None = None,
    *,
    runtime_data_dir: Path | str | None = None,
    require_wecom: bool = True,
) -> CheckReport:
    root = Path(runtime_data_dir) if runtime_data_dir is not None else data_dir()
    report = run_runtime_check(root, require_wecom=require_wecom)
    target = (
        Path(report_path)
        if report_path is not None
        else data_dir() / "diagnostics" / "runtime-check.json"
    )
    report.write(target)
    return report


def run_api_check(report_path: Path | str | None = None) -> CheckReport:
    errors: list[str] = []
    details: dict[str, object] = {}
    try:
        from candidate_judge import CandidateJudge
        from memory import StyleMemory
        from settings import AppSettings
        from tools.knowledge_audit import KNOWN_QUESTION

        settings = AppSettings().load()
        memory = StyleMemory().load()
        candidates = memory.knowledge_repository().candidates(KNOWN_QUESTION, KNOWN_QUESTION)
        if not candidates:
            errors.append("known_candidate_missing")
        else:
            judgement = CandidateJudge(settings).judge(KNOWN_QUESTION, candidates)
            details["candidate_count"] = len(candidates)
            details["strict_judgement_received"] = judgement is not None
            if judgement is None or not judgement.consistent:
                errors.append("candidate_judge_failed")
    except Exception as error:
        errors.append(f"api_check:{type(error).__name__}")
    report = CheckReport(kind="api", ok=not errors, errors=tuple(errors), details=details)
    target = (
        Path(report_path)
        if report_path is not None
        else data_dir() / "diagnostics" / "api-check.json"
    )
    report.write(target)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="聊小智结构化自检")
    parser.add_argument("--package", action="store_true")
    parser.add_argument("--api", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-wecom", action="store_true")
    args = parser.parse_args(argv)
    if args.package:
        report = run_package_self_check(args.report)
    elif args.api:
        report = run_api_check(args.report)
    else:
        report = run_self_check(
            args.report,
            runtime_data_dir=args.data_dir,
            require_wecom=not args.no_wecom,
        )
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
