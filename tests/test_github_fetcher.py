from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests.exceptions
from github.GithubException import GithubException, RateLimitExceededException

from dci_report_gen.config import SourceConfig
from dci_report_gen.fetchers.github import GitHubFetcher


def _make_source(**kwargs):
    defaults = {
        "type": "github",
        "query": "repo:owner/repo is:issue is:open",
        "fields": None,
        "max_results": 100,
    }
    defaults.update(kwargs)
    return SourceConfig(**defaults)


def _make_issue(
    number=1,
    title="Test issue",
    state="open",
    login="user1",
    html_url="https://github.com/owner/repo/issues/1",
):
    issue = MagicMock()
    issue.number = number
    issue.title = title
    issue.state = state
    issue.user.login = login
    issue.html_url = html_url
    issue.created_at = None
    issue.updated_at = None
    issue.labels = []
    issue.assignees = []
    return issue


@patch("dci_report_gen.fetchers.github._get_client")
def test_fetch_returns_results(mock_get_client):
    issue = _make_issue(number=42, title="Fix bug", state="open", login="alice")
    mock_client = MagicMock()
    mock_client.search_issues.return_value = [issue]
    mock_get_client.return_value = mock_client

    fetcher = GitHubFetcher()
    source = _make_source(query="repo:owner/repo is:issue")
    results = fetcher.fetch(source)

    assert len(results) == 1
    assert results[0]["number"] == 42
    assert results[0]["title"] == "Fix bug"
    assert results[0]["state"] == "open"
    assert results[0]["author"] == "alice"


@patch("dci_report_gen.fetchers.github._get_client")
def test_fetch_empty_query_returns_empty(mock_get_client):
    fetcher = GitHubFetcher()
    source = _make_source(query=None)
    results = fetcher.fetch(source)

    assert results == []
    mock_get_client.assert_not_called()


@patch("dci_report_gen.fetchers.github._get_client")
def test_fetch_rate_limit_raises_runtime_error(mock_get_client):
    mock_client = MagicMock()
    mock_client.search_issues.side_effect = RateLimitExceededException(
        403, "rate limit exceeded", {}
    )
    mock_get_client.return_value = mock_client

    fetcher = GitHubFetcher()
    source = _make_source()
    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        fetcher.fetch(source)


@patch("dci_report_gen.fetchers.github._get_client")
def test_fetch_retry_exhausted_raises_runtime_error(mock_get_client):
    mock_client = MagicMock()
    mock_client.search_issues.side_effect = requests.exceptions.RetryError(
        "Max retries exceeded"
    )
    mock_get_client.return_value = mock_client

    fetcher = GitHubFetcher()
    source = _make_source()
    with pytest.raises(RuntimeError, match="request failed after retries"):
        fetcher.fetch(source)


@patch("dci_report_gen.fetchers.github._get_client")
def test_fetch_github_exception_raises_runtime_error(mock_get_client):
    mock_client = MagicMock()
    mock_client.search_issues.side_effect = GithubException(
        422, "Unprocessable Entity", {}
    )
    mock_get_client.return_value = mock_client

    fetcher = GitHubFetcher()
    source = _make_source()
    with pytest.raises(RuntimeError, match="GitHub API error"):
        fetcher.fetch(source)


@patch("dci_report_gen.fetchers.github._get_client")
def test_fetch_custom_fields(mock_get_client):
    issue = _make_issue(number=7, html_url="https://github.com/owner/repo/issues/7")
    mock_client = MagicMock()
    mock_client.search_issues.return_value = [issue]
    mock_get_client.return_value = mock_client

    fetcher = GitHubFetcher()
    source = _make_source(fields=["number", "url"])
    results = fetcher.fetch(source)

    assert len(results) == 1
    assert results[0]["number"] == 7
    assert results[0]["url"] == "https://github.com/owner/repo/issues/7"
    assert "title" not in results[0]
    assert "author" not in results[0]


@patch("dci_report_gen.fetchers.github._get_client")
def test_fetch_respects_max_results(mock_get_client):
    issues = [_make_issue(number=i, title=f"Issue {i}") for i in range(10)]
    mock_client = MagicMock()
    mock_client.search_issues.return_value = issues
    mock_get_client.return_value = mock_client

    fetcher = GitHubFetcher()
    source = _make_source(max_results=3)
    results = fetcher.fetch(source)

    assert len(results) == 3
    assert results[0]["number"] == 0
    assert results[1]["number"] == 1
    assert results[2]["number"] == 2
