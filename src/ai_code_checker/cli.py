"""Command line entry point: `ai-code-checker github` in CI, `ai-code-checker local` on a laptop."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .diff import parse_unified_diff
from .models import SEVERITY_ORDER, Report
from .report import render
from .reviewer import DEFAULT_MODEL, ClaudeClient, ModelClient, ReviewError, review_change
from .static_checks import run_static_checks
from .tests_runner import run_tests


def build_report(
    title: str, body: str, diff_text: str, root: Path, client: ModelClient | None, test_command: str | None
) -> Report:
    files = parse_unified_diff(diff_text)
    report = Report(title=title, static_findings=run_static_checks(files, root))
    if client is not None:
        try:
            report.ai, report.skipped_files = review_change(client, title, body, files, root)
        except ReviewError as e:
            report.ai_error = str(e)
        except Exception as e:  # The automatic checks are still worth posting if the API is down.
            report.ai_error = f"{type(e).__name__}: {e}."
    if test_command:
        report.tests = run_tests(test_command, root)
    return report


def should_fail(report: Report, fail_on: str) -> bool:
    if fail_on == "never":
        return False
    if report.tests and not report.tests.passed:
        return True
    limit = SEVERITY_ORDER[fail_on]
    return any(SEVERITY_ORDER[f.severity] <= limit for f in report.all_findings())


def _client(args: argparse.Namespace) -> ModelClient | None:
    if args.no_ai:
        return None
    return ClaudeClient(model=args.model, effort=args.effort)


def cmd_local(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    diff_text = subprocess.run(
        ["git", "diff", f"{args.base}...HEAD" if not args.uncommitted else "HEAD"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    if not diff_text.strip():
        print("No changes to review.")
        return 0
    title = args.title or subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    report = build_report(title, args.description or "", diff_text, root, _client(args), args.test_command)
    print(render(report))
    return 1 if should_fail(report, args.fail_on) else 0


def cmd_github(args: argparse.Namespace) -> int:
    from .github import GitHub

    root = Path(args.root).resolve()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    number = (event.get("pull_request") or {}).get("number") or event.get("number")
    if not number:
        print("Not a pull_request event; nothing to review.")
        return 0
    gh = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"], os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    pr = gh.pull_request(int(number))
    report = build_report(pr.title, pr.body, gh.diff(pr, root), root, _client(args), args.test_command)
    inline = gh.post_inline(pr, report.all_findings())
    body = render(report, inline)
    gh.upsert_summary(pr, body)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a") as fh:
            fh.write(body + "\n")
    return 1 if should_fail(report, args.fail_on) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ai-code-checker", description="Explain, check and fix AI-written code changes.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("local", "github"):
        s = sub.add_parser(name)
        s.add_argument("--root", default=".", help="Repository root.")
        s.add_argument("--model", default=os.environ.get("AI_CODE_CHECKER_MODEL", DEFAULT_MODEL))
        s.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
        s.add_argument("--test-command", default=None, help="Command that runs the project's tests, e.g. 'pytest -q'.")
        s.add_argument("--fail-on", default="high", choices=["high", "medium", "low", "never"],
                       help="Exit non-zero when an issue this severe or worse is found, or tests fail.")
        s.add_argument("--no-ai", action="store_true", help="Run only the automatic checks, without Claude.")
        if name == "local":
            s.add_argument("--base", default="main", help="Branch to compare against.")
            s.add_argument("--uncommitted", action="store_true", help="Review uncommitted changes instead.")
            s.add_argument("--title", default=None, help="What the change is meant to do.")
            s.add_argument("--description", default=None)
    args = p.parse_args(argv)
    return cmd_local(args) if args.cmd == "local" else cmd_github(args)


if __name__ == "__main__":
    sys.exit(main())
