"""Data shapes shared by the static checks, the Claude review and the report."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low"]
Category = Literal["bug", "security", "scope", "invented", "leftover", "tests", "other"]

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class Finding(BaseModel):
    path: str = Field(description="File path exactly as it appears in the diff.")
    line: int | None = Field(
        description="New-side line number from the numbered diff that the finding is about, or null if it is about the whole file."
    )
    severity: Severity = Field(
        description="high = will break or is exploitable; medium = likely wrong or risky; low = worth a look."
    )
    category: Category = Field(
        description="bug, security, scope (unrelated to the PR's goal), invented (APIs, packages or functions that do not exist), leftover (placeholder, debug or dead code), tests, other."
    )
    title: str = Field(description="One short sentence naming the problem.")
    explanation: str = Field(description="Plain-English why: what goes wrong, and when.")
    suggestion: str | None = Field(
        default=None,
        description="Replacement code for exactly the cited line (one line only), or null. No code fences.",
    )
    source: Literal["static", "ai"] = "ai"


class FileExplanation(BaseModel):
    path: str
    summary: str = Field(description="What changed in this file and why it matters, in one to three plain sentences.")


class SuggestedTest(BaseModel):
    path: str = Field(description="Where the test file should live.")
    reason: str = Field(description="Which changed behaviour it covers.")
    code: str = Field(description="The complete test code.")


class AIReview(BaseModel):
    overview: str = Field(description="Two to four plain sentences: what this change does overall, for an engineer who has not read it.")
    matches_stated_goal: bool = Field(description="True if every change serves the PR's stated goal.")
    files: list[FileExplanation]
    findings: list[Finding]
    suggested_tests: list[SuggestedTest]


class TestRun(BaseModel):
    command: str
    passed: bool
    exit_code: int
    output_tail: str
    timed_out: bool = False


class Report(BaseModel):
    title: str
    ai: AIReview | None = None
    ai_error: str | None = None
    static_findings: list[Finding] = []
    tests: TestRun | None = None
    skipped_files: list[str] = []

    def all_findings(self) -> list[Finding]:
        found = list(self.static_findings) + (list(self.ai.findings) if self.ai else [])
        return sorted(found, key=lambda f: (SEVERITY_ORDER[f.severity], f.path, f.line or 0))
