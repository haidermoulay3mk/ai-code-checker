"""Turn a Report into the Markdown posted on the pull request."""

from __future__ import annotations

from .models import Finding, Report

MARKER = "<!-- ai-code-checker -->"
ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
CATEGORY_LABEL = {
    "bug": "Bug", "security": "Security", "scope": "Not part of this change", "invented": "Doesn't exist",
    "leftover": "Leftover", "tests": "Missing test", "other": "Other",
}


def _where(f: Finding) -> str:
    return f"`{f.path}:{f.line}`" if f.line else f"`{f.path}`"


def finding_comment(f: Finding) -> str:
    """Body of an inline review comment. Includes a one-click suggestion when there is one."""
    body = f"{ICON[f.severity]} **{CATEGORY_LABEL[f.category]}: {f.title}**\n\n{f.explanation}"
    if f.suggestion is not None:
        body += f"\n\n```suggestion\n{f.suggestion}\n```"
    return body + f"\n\n<sub>ai-code-checker · {'automatic check' if f.source == 'static' else 'Claude review'}</sub>"


def render(report: Report, inline_keys: set[tuple[str, int]] | None = None) -> str:
    inline_keys = inline_keys or set()
    findings = report.all_findings()
    counts = {s: sum(1 for f in findings if f.severity == s) for s in ("high", "medium", "low")}
    lines = [MARKER, "## 🔍 AI Code Checker", ""]

    if findings:
        lines.append(
            f"**{len(findings)} issue{'s' if len(findings) != 1 else ''} found:** "
            f"{ICON['high']} {counts['high']} high · {ICON['medium']} {counts['medium']} medium · {ICON['low']} {counts['low']} low"
        )
    else:
        lines.append("**No issues found.**")
    if report.tests:
        t = report.tests
        state = "⏱️ timed out" if t.timed_out else ("✅ passed" if t.passed else f"❌ failed (exit {t.exit_code})")
        lines.append(f"**Tests:** `{t.command}` {state}")
    lines.append("")
    lines.append("_This bot explains and flags. It never approves or merges; a person decides._")
    lines.append("")

    if report.ai:
        ai = report.ai
        lines += ["### What this change does", "", ai.overview, ""]
        if not ai.matches_stated_goal:
            lines += ["> ⚠️ **Some of this change does not match what the pull request says it does.** See the \"Not part of this change\" items below.", ""]
        if ai.files:
            lines += ["<details><summary><b>File by file</b></summary>", ""]
            lines += [f"- `{fe.path}`: {fe.summary}" for fe in ai.files]
            lines += ["", "</details>", ""]
    elif report.ai_error:
        lines += [f"> ⚠️ The Claude review did not run: {report.ai_error} The automatic checks below still ran.", ""]

    if findings:
        lines += ["### Issues", ""]
        for f in findings:
            inline = " (commented inline)" if f.line and (f.path, f.line) in inline_keys else ""
            lines.append(f"- {ICON[f.severity]} **{CATEGORY_LABEL[f.category]}** {_where(f)}{inline}: {f.title}")
            if not inline:
                lines.append(f"  <br>{f.explanation}")
        lines.append("")

    if report.ai and report.ai.suggested_tests:
        lines += ["### Suggested tests", ""]
        for t in report.ai.suggested_tests:
            lines += [f"<details><summary><code>{t.path}</code>: {t.reason}</summary>", "", "````", t.code, "````", "", "</details>", ""]

    if report.tests and not report.tests.passed and report.tests.output_tail:
        lines += ["<details><summary><b>Test output (last lines)</b></summary>", "", "````", report.tests.output_tail, "````", "", "</details>", ""]

    if report.skipped_files:
        lines += ["_Too large to review: " + ", ".join(f"`{p}`" for p in report.skipped_files) + "_", ""]
    return "\n".join(lines)
