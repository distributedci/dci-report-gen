from __future__ import annotations

import itertools
import os

import requests.exceptions
from github import Auth, Github, GithubRetry
from github.GithubException import GithubException, RateLimitExceededException

from dci_report_gen.config import SourceConfig

_DEFAULT_FIELDS = ["number", "title", "state", "author"]

# GitHub Search API allows 30 req/min for authenticated users
_SEARCH_SECONDS_BETWEEN_REQUESTS: float = 1.0


def _get_client():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable is required")
    retry = GithubRetry(total=5, backoff_factor=1.0)
    return Github(
        auth=Auth.Token(token),
        retry=retry,
        seconds_between_requests=_SEARCH_SECONDS_BETWEEN_REQUESTS,
        seconds_between_writes=1.0,
    )


def _extract_field(issue, field_name: str):
    if field_name == "author":
        return issue.user.login if issue.user else ""
    if field_name == "created_at":
        return issue.created_at.isoformat() if issue.created_at else ""
    if field_name == "updated_at":
        return issue.updated_at.isoformat() if issue.updated_at else ""
    if field_name == "labels":
        return ", ".join(label.name for label in issue.labels)
    if field_name == "assignees":
        return ", ".join(a.login for a in issue.assignees)
    if field_name == "url":
        return issue.html_url
    return getattr(issue, field_name, "")


class GitHubFetcher:
    def fetch(self, source: SourceConfig) -> list[dict]:
        if not source.query:
            return []

        try:
            client = _get_client()
            results = client.search_issues(source.query)

            fields = source.fields or _DEFAULT_FIELDS
            return [
                {f: _extract_field(issue, f) for f in fields}
                for issue in itertools.islice(results, source.max_results)
            ]
        except RateLimitExceededException as exc:
            raise RuntimeError(
                f"GitHub Search API rate limit exceeded after retries: {exc}"
            ) from exc
        except GithubException as exc:
            raise RuntimeError(f"GitHub API error: {exc}") from exc
        except (
            requests.exceptions.RetryError,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as exc:
            raise RuntimeError(
                f"GitHub request failed after retries: {exc}"
            ) from exc
