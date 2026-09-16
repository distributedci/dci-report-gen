"""Tests for the Jira fetcher, including rate-limit retry handling."""
from __future__ import annotations

import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
import requests

from dci_report_gen.config import SourceConfig
from dci_report_gen.fetchers.jira import (
    JiraFetcher,
    _TimeoutHTTPAdapter,
    _REQUEST_TIMEOUT,
    _RETRY_STATUS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(**kwargs) -> SourceConfig:
    defaults = dict(
        type="jira",
        jql="project = TEST ORDER BY updated DESC",
        max_results=10,
        fields=["key", "summary", "status", "assignee"],
    )
    defaults.update(kwargs)
    return SourceConfig(**defaults)


def _make_mock_issue(key="TEST-1", summary="Test issue", status="Open", assignee="alice"):
    issue = MagicMock()
    issue.key = key
    issue.fields.summary = summary
    issue.fields.status.__str__ = lambda s: status
    issue.fields.assignee.__str__ = lambda s: assignee
    return issue


# ---------------------------------------------------------------------------
# Test 1: normal fetch returns expected rows
# ---------------------------------------------------------------------------


def test_fetch_returns_rows():
    """fetch() should return one dict per issue with the requested fields."""
    fetcher = JiraFetcher()
    source = _make_source()

    mock_issue = _make_mock_issue()

    with patch("dci_report_gen.fetchers.jira.JIRA") as MockJIRA:
        mock_client = MagicMock()
        MockJIRA.return_value = mock_client
        mock_client.search_issues.return_value = [mock_issue]
        # Provide a real-ish _session so mount() calls don't blow up
        mock_client._session = MagicMock()

        with patch.dict(os.environ, {"JIRA_TOKEN": "tok", "JIRA_EMAIL": "user@example.com"}):
            rows = fetcher.fetch(source)

    assert len(rows) == 1
    assert rows[0]["key"] == "TEST-1"
    assert rows[0]["summary"] == "Test issue"


# ---------------------------------------------------------------------------
# Test 2: empty JQL returns empty list without touching JIRA
# ---------------------------------------------------------------------------


def test_fetch_empty_jql_returns_empty():
    """fetch() must short-circuit and return [] when jql is falsy."""
    fetcher = JiraFetcher()
    source = _make_source(jql="")

    with patch("dci_report_gen.fetchers.jira.JIRA") as MockJIRA:
        result = fetcher.fetch(source)
        MockJIRA.assert_not_called()

    assert result == []


# ---------------------------------------------------------------------------
# Test 3: adapter is configured with correct retry settings
# ---------------------------------------------------------------------------


def test_retry_adapter_configured():
    """_get_client() must mount a _TimeoutHTTPAdapter with correct retry settings."""
    fetcher = JiraFetcher()

    with patch("dci_report_gen.fetchers.jira.JIRA") as MockJIRA:
        mock_client = MagicMock()
        MockJIRA.return_value = mock_client
        mock_session = MagicMock()
        mock_client._session = mock_session

        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            fetcher._get_client()

    # mount() should have been called at least for https:// and http://
    mount_calls = mock_session.mount.call_args_list
    schemes = [c[0][0] for c in mount_calls]
    assert "https://" in schemes
    assert "http://" in schemes

    # Grab the adapter used for https://
    https_adapter = next(c[0][1] for c in mount_calls if c[0][0] == "https://")

    assert isinstance(https_adapter, _TimeoutHTTPAdapter)
    assert https_adapter._timeout == _REQUEST_TIMEOUT

    retry = https_adapter.max_retries
    assert retry.respect_retry_after_header is True
    assert set(retry.status_forcelist) == _RETRY_STATUS


# ---------------------------------------------------------------------------
# Test 4: adapter retries on 429 then succeeds on 200
# ---------------------------------------------------------------------------


def test_adapter_retries_on_429_then_succeeds():
    """A real session with the adapter should retry once after a 429 response."""
    from urllib3.util.retry import Retry
    from urllib3.response import HTTPResponse as Urllib3Response

    retry = Retry(
        total=2,
        backoff_factor=0,
        status_forcelist=[429],
        respect_retry_after_header=False,
        raise_on_status=False,
    )
    adapter = _TimeoutHTTPAdapter(timeout=5, max_retries=retry)
    session = requests.Session()
    session.mount("http://", adapter)

    call_count = 0

    def fake_make_request(self, conn, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        status = 429 if call_count == 1 else 200
        body = BytesIO(b"")
        resp = Urllib3Response(status=status, body=body, headers={}, preload_content=False)
        return resp

    with patch("urllib3.connectionpool.HTTPConnectionPool._make_request", fake_make_request):
        resp = session.get("http://test.example.com/issues")

    assert resp.status_code == 200
    assert call_count == 2


# ---------------------------------------------------------------------------
# Test 5: RetryError surfaces as RuntimeError with clear message
# ---------------------------------------------------------------------------


def test_retry_exhausted_surfaces_clear_error():
    """fetch() must convert RetryError to a RuntimeError describing retries."""
    fetcher = JiraFetcher()
    source = _make_source()

    with patch("dci_report_gen.fetchers.jira.JIRA") as MockJIRA:
        mock_client = MagicMock()
        MockJIRA.return_value = mock_client
        mock_client._session = MagicMock()
        mock_client.search_issues.side_effect = requests.exceptions.RetryError("exhausted")

        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            with pytest.raises(RuntimeError, match="after retries"):
                fetcher.fetch(source)


# ---------------------------------------------------------------------------
# Test 6: Timeout surfaces as RuntimeError with clear message
# ---------------------------------------------------------------------------


def test_timeout_surfaces_clear_error():
    """fetch() must convert Timeout to a RuntimeError describing a timeout."""
    fetcher = JiraFetcher()
    source = _make_source()

    with patch("dci_report_gen.fetchers.jira.JIRA") as MockJIRA:
        mock_client = MagicMock()
        MockJIRA.return_value = mock_client
        mock_client._session = MagicMock()
        mock_client.search_issues.side_effect = requests.exceptions.Timeout("too slow")

        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            with pytest.raises(RuntimeError, match="timed out"):
                fetcher.fetch(source)


# ---------------------------------------------------------------------------
# Test 7: missing token raises RuntimeError mentioning JIRA_TOKEN
# ---------------------------------------------------------------------------


def test_missing_token_raises():
    """_get_client() must raise RuntimeError when no token env var is set."""
    fetcher = JiraFetcher()

    env_without_token = {
        k: v
        for k, v in os.environ.items()
        if k not in ("JIRA_TOKEN", "JIRA_API_TOKEN")
    }

    with patch.dict(os.environ, env_without_token, clear=True):
        with pytest.raises(RuntimeError, match="JIRA_TOKEN"):
            fetcher._get_client()
