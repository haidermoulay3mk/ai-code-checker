"""The Claude client and the GitHub client, against fake HTTP servers."""

import json

import anthropic
import httpx2 as httpx
import pytest

from ai_code_checker.github import GitHub, PullRequest
from ai_code_checker.models import Finding
from ai_code_checker.report import MARKER
from ai_code_checker.reviewer import ClaudeClient, ReviewError

REVIEW = {
    "overview": "Adds a discount.", "matches_stated_goal": True, "files": [],
    "findings": [{"path": "a.py", "line": 3, "severity": "high", "category": "bug",
                  "title": "Off by one", "explanation": "e", "suggestion": None}],
    "suggested_tests": [],
}


def _sse(stop_reason: str) -> str:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": json.dumps(REVIEW)}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                           "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


@pytest.fixture
def fake_claude(monkeypatch):
    sent = {}

    def install(stop_reason="end_turn"):
        def handler(req):
            sent["body"] = json.loads(req.content)
            sent["beta"] = req.headers.get("anthropic-beta")
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=_sse(stop_reason))

        real = anthropic.Anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: real(
            api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0))
        return sent

    return install


def test_claude_request_shape_and_parsing(fake_claude):
    sent = fake_claude()
    review = ClaudeClient().review("system", "prompt")
    assert review.findings[0].title == "Off by one"
    body = sent["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default" and sent["beta"] == "server-side-fallback-2026-07-01"


def test_claude_refusal_is_a_review_error(fake_claude):
    fake_claude("refusal")
    with pytest.raises(ReviewError):
        ClaudeClient().review("system", "prompt")


class FakeResponse:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text, self.links = status, data, text, {}
        self.ok = status < 400

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, review_status=200):
        self.headers = {}
        self.calls = []
        self.issue_comments = [{"id": 7, "body": f"{MARKER}\nold"}]
        self.review_comments = [{"path": "a.py", "line": 3, "body": "🔴 **Bug: Already said**\n\nx"}]
        self.review_status = review_status

    def get(self, url, **kw):
        self.calls.append(("GET", url, None))
        if "/issues/1/comments" in url:
            return FakeResponse(data=self.issue_comments)
        if "/pulls/1/comments" in url:
            return FakeResponse(data=self.review_comments)
        raise AssertionError(url)

    def post(self, url, json=None, **kw):
        self.calls.append(("POST", url, json))
        return FakeResponse(self.review_status if url.endswith("/reviews") else 201)

    def patch(self, url, json=None, **kw):
        self.calls.append(("PATCH", url, json))
        return FakeResponse(200)


PR = PullRequest(number=1, title="t", body="", base_sha="b", head_sha="h", base_ref="main")


def _gh(session) -> GitHub:
    gh = GitHub("token", "o/r")
    gh.s = session
    return gh


def test_inline_review_is_comment_only_and_skips_repeats():
    s = FakeSession()
    findings = [
        Finding(path="a.py", line=3, severity="high", category="bug", title="Already said", explanation="x"),
        Finding(path="a.py", line=5, severity="low", category="bug", title="New one", explanation="y"),
        Finding(path="a.py", line=None, severity="low", category="scope", title="Whole file", explanation="z"),
    ]
    keys = _gh(s).post_inline(PR, findings)
    assert keys == {("a.py", 3), ("a.py", 5)}
    method, url, payload = s.calls[-1]
    assert url.endswith("/pulls/1/reviews")
    assert payload["event"] == "COMMENT"  # never APPROVE or REQUEST_CHANGES
    assert [c["line"] for c in payload["comments"]] == [5]


def test_inline_review_rejected_falls_back_to_summary_only():
    s = FakeSession(review_status=422)
    f = Finding(path="a.py", line=9, severity="low", category="bug", title="t", explanation="e")
    assert _gh(s).post_inline(PR, [f]) == set()


def test_summary_comment_is_updated_not_duplicated():
    s = FakeSession()
    _gh(s).upsert_summary(PR, f"{MARKER}\nnew")
    assert s.calls[-1][0] == "PATCH" and s.calls[-1][1].endswith("/issues/comments/7")
    s.issue_comments = []
    _gh(s).upsert_summary(PR, f"{MARKER}\nnew")
    assert s.calls[-1][0] == "POST"
