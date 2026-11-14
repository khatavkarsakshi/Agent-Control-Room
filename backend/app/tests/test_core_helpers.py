"""Retry helper and input sanitisation tests."""

from __future__ import annotations

import pytest

from app.core.retry import retry_call
from app.core.sanitization import escape_like, safe_filename, sanitize_text


def test_retry_call_returns_first_success(monkeypatch):
    monkeypatch.setattr("app.core.retry.time.sleep", lambda _delay: None)
    calls: list[int] = []

    def _flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("temporary")
        return "ok"

    result = retry_call(_flaky, retries=4, base_delay=0.01, max_delay=0.1)

    assert result == "ok"
    assert len(calls) == 3


def test_retry_call_honours_retry_predicate(monkeypatch):
    monkeypatch.setattr("app.core.retry.time.sleep", lambda _delay: None)
    attempts: list[int] = []

    def _fatal() -> None:
        attempts.append(1)
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        retry_call(
            _fatal,
            retries=5,
            base_delay=0.01,
            max_delay=0.1,
            retry_if=lambda exc: isinstance(exc, ConnectionError),
        )

    assert len(attempts) == 1


def test_retry_call_exhausts_budget(monkeypatch):
    monkeypatch.setattr("app.core.retry.time.sleep", lambda _delay: None)
    attempts: list[int] = []

    def _always_fails() -> None:
        attempts.append(1)
        raise ConnectionError("still down")

    with pytest.raises(ConnectionError):
        retry_call(_always_fails, retries=2, base_delay=0.01, max_delay=0.1)

    assert len(attempts) == 3  # initial attempt + two retries


def test_retry_call_reports_attempts(monkeypatch):
    monkeypatch.setattr("app.core.retry.time.sleep", lambda _delay: None)
    monkeypatch.setattr("app.core.retry.random.uniform", lambda _a, _b: 0.0)
    seen: list[int] = []

    def _on_retry(attempt: int, _exc: Exception, delay: float) -> None:
        seen.append(attempt)
        assert delay == 0.0

    def _flaky() -> str:
        if len(seen) < 2:
            raise ConnectionError("temporary")
        return "done"

    assert retry_call(
        _flaky, retries=2, base_delay=1, max_delay=3, on_retry=_on_retry
    ) == "done"
    assert seen == [1, 2]


def test_sanitize_text_normalises_control_characters():
    assert sanitize_text("  hello\x00 world  ") == "hello world"
    assert sanitize_text("line1\r\nline2\rline3") == "line1\nline2\nline3"
    assert sanitize_text("multi\n\n\n\n\nline") == "multi\n\n\nline"
    assert sanitize_text("a\nb", allow_newlines=False) == "a b"


def test_sanitize_text_enforces_length():
    with pytest.raises(ValueError, match="maximum length"):
        sanitize_text("x" * 11, max_length=10)


def test_escape_like_escapes_all_wildcards():
    assert escape_like("100%_done\\") == "100\\%\\_done\\\\"


def test_safe_filename_removes_path_traversal():
    assert safe_filename("../../etc/passwd") == "etc-passwd"
    assert safe_filename("***") == "export"
