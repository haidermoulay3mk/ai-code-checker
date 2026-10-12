"""Read a pull request and post the review back to it, through the GitHub REST API."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

from .models import Finding
from .report import MARKER, finding_comment

API = "https://api.github.com"
MAX_INLINE_COMMENTS = 30


@dataclass
class PullRequest:
    number: int
    title: str
    body: str
    base_sha: str
    head_sha: str
    base_ref: str


class GitHub:
    def __init__(self, token: str, repo: str, api_url: str = API):
        self.repo = repo
        self.api = api_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _url(self, path: str) -> str:
        return f"{self.api}/repos/{self.repo}{path}"

    def _paginate(self, path: str) -> list[dict]:
        items: list[dict] = []
        url: str | None = self._url(path) + "?per_page=100"
        while url:
            r = self.s.get(url, timeout=30)
            r.raise_for_status()
            items += r.json()
            url = r.links.get("next", {}).get("url")
        return items

    def pull_request(self, number: int) -> PullRequest:
        r = self.s.get(self._url(f"/pulls/{number}"), timeout=30)
        r.raise_for_status()
        d = r.json()
        return PullRequest(
            number=number, title=d["title"], body=d.get("body") or "",
            base_sha=d["base"]["sha"], head_sha=d["head"]["sha"], base_ref=d["base"]["ref"],
        )

    def diff(self, pr: PullRequest, root: Path) -> str:
        r = self.s.get(self._url(f"/pulls/{pr.number}"), headers={"Accept": "application/vnd.github.diff"}, timeout=60)
        if r.ok:
            return r.text
        # GitHub refuses to render very large diffs; fall back to the local checkout.
        return subprocess.run(
            ["git", "diff", f"{pr.base_sha}...{pr.head_sha}"], cwd=root, capture_output=True, text=True, check=True
        ).stdout

    def upsert_summary(self, pr: PullRequest, body: str) -> None:
        for c in self._paginate(f"/issues/{pr.number}/comments"):
            if MARKER in (c.get("body") or ""):
                self.s.patch(self._url(f"/issues/comments/{c['id']}"), json={"body": body}, timeout=30).raise_for_status()
                return
        self.s.post(self._url(f"/issues/{pr.number}/comments"), json={"body": body}, timeout=30).raise_for_status()

    def post_inline(self, pr: PullRequest, findings: list[Finding]) -> set[tuple[str, int]]:
        """Post line comments as one review with event COMMENT: it never approves or blocks."""
        existing = {
            (c.get("path"), c.get("line"), (c.get("body") or "").split("\n", 1)[0])
            for c in self._paginate(f"/pulls/{pr.number}/comments")
        }
        comments, keys = [], set()
        for f in findings:
            if f.line is None or len(comments) >= MAX_INLINE_COMMENTS:
                continue
            body = finding_comment(f)
            keys.add((f.path, f.line))
            if (f.path, f.line, body.split("\n", 1)[0]) in existing:
                continue  # Already said on an earlier run.
            comments.append({"path": f.path, "line": f.line, "side": "RIGHT", "body": body})
        if not comments:
            return keys
        r = self.s.post(
            self._url(f"/pulls/{pr.number}/reviews"),
            json={"commit_id": pr.head_sha, "event": "COMMENT", "comments": comments},
            timeout=60,
        )
        if r.status_code == 422:
            return set()  # A line moved under us; everything still appears in the summary.
        r.raise_for_status()
        return keys
