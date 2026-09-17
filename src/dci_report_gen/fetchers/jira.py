from __future__ import annotations

import os

import requests
from jira import JIRA
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dci_report_gen.config import SourceConfig

_REQUEST_TIMEOUT = 60
_RETRY_TOTAL = 5
_RETRY_BACKOFF = 1
_RETRY_STATUS = frozenset({429, 503})

_FIELD_MAP = {
    "key": lambda issue: issue.key,
    "summary": lambda issue: issue.fields.summary,
    "status": lambda issue: str(issue.fields.status),
    "assignee": lambda issue: str(issue.fields.assignee) if issue.fields.assignee else "",
    "reporter": lambda issue: str(issue.fields.reporter) if issue.fields.reporter else "",
    "priority": lambda issue: str(issue.fields.priority) if issue.fields.priority else "",
    "issue_type": lambda issue: str(issue.fields.issuetype),
    "created": lambda issue: issue.fields.created,
    "updated": lambda issue: issue.fields.updated,
    "labels": lambda issue: ", ".join(issue.fields.labels),
    "components": lambda issue: ", ".join(str(c) for c in issue.fields.components),
    "fix_versions": lambda issue: ", ".join(str(v) for v in issue.fields.fixVersions),
}

_DEFAULT_FIELDS = ["key", "summary", "status", "assignee"]


class _TimeoutHTTPAdapter(HTTPAdapter):
    """HTTPAdapter that injects a default timeout on every request."""

    def __init__(self, timeout, *args, **kwargs):
        self._timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().send(*args, **kwargs)


def _extract_field(issue, field_name: str) -> str:
    if field_name in _FIELD_MAP:
        return _FIELD_MAP[field_name](issue)
    attr = getattr(issue.fields, field_name, None)
    if attr is not None:
        return str(attr)
    return ""


class JiraFetcher:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            url = os.environ.get("JIRA_URL", "https://redhat.atlassian.net")
            token = os.environ.get("JIRA_TOKEN") or os.environ.get("JIRA_API_TOKEN")
            email = os.environ.get("JIRA_EMAIL")
            if not token:
                raise RuntimeError(
                    "JIRA_TOKEN or JIRA_API_TOKEN environment variable is required"
                )
            if email:
                self._client = JIRA(server=url, basic_auth=(email, token))
            else:
                self._client = JIRA(server=url, token_auth=token)

            retry = Retry(
                total=_RETRY_TOTAL,
                backoff_factor=_RETRY_BACKOFF,
                status_forcelist=list(_RETRY_STATUS),
                respect_retry_after_header=True,
                raise_on_status=False,
            )
            adapter = _TimeoutHTTPAdapter(timeout=_REQUEST_TIMEOUT, max_retries=retry)
            self._client._session.mount("https://", adapter)
            self._client._session.mount("http://", adapter)

        return self._client

    def fetch(self, source: SourceConfig) -> list[dict]:
        if not source.jql:
            return []

        client = self._get_client()
        try:
            issues = client.search_issues(
                source.jql,
                maxResults=source.max_results,
            )
        except requests.exceptions.RetryError as exc:
            raise RuntimeError(f"Jira request failed after retries: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(f"Jira request timed out: {exc}") from exc

        report_fields = source.fields or _DEFAULT_FIELDS
        return [{f: _extract_field(issue, f) for f in report_fields} for issue in issues]
