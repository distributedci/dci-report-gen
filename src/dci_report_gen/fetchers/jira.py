from __future__ import annotations

import os

import requests

from dci_report_gen.config import SourceConfig

# Human-readable aliases mapped to Jira custom field ids.
_CUSTOM_FIELD_ALIASES = {
    "release_type": "customfield_10851",
    "planning": "customfield_10553",
    "target_version": "customfield_10855",
    "qa_contact": "customfield_10470",
    "severity": "customfield_10840",
    "story_points": "customfield_10028",
    "team": "customfield_10001",
}

# Report field name -> Jira REST field id to request from the API.
_REST_FIELD_MAP = {
    "key": "key",
    "summary": "summary",
    "status": "status",
    "assignee": "assignee",
    "reporter": "reporter",
    "priority": "priority",
    "issue_type": "issuetype",
    "created": "created",
    "updated": "updated",
    "labels": "labels",
    "components": "components",
    "fix_versions": "fixVersions",
    "parent": "parent",
    "parent_summary": "parent",
}

_DEFAULT_FIELDS = ["key", "summary", "status", "assignee"]


def _rest_field_id(name: str) -> str:
    """Resolve a report field name to the Jira REST field id to request."""
    if name in _REST_FIELD_MAP:
        return _REST_FIELD_MAP[name]
    return _CUSTOM_FIELD_ALIASES.get(name, name)


def _extract_option(attr) -> str:
    """Extract a readable value from a Jira custom field (option, list, or scalar)."""
    if attr is None:
        return ""
    if isinstance(attr, dict):
        # Option objects expose "value" or "name"
        for k in ("value", "name", "displayName"):
            if k in attr and attr[k] is not None:
                return str(attr[k])
        return ""
    if isinstance(attr, (list, tuple)):
        return ", ".join(_extract_option(item) for item in attr)
    return str(attr)


def _extract_field(issue: dict, field_name: str) -> str:
    """Extract a report value from a raw Jira issue JSON dict."""
    fields = issue.get("fields", {}) or {}

    if field_name == "key":
        return issue.get("key", "")
    if field_name == "summary":
        return fields.get("summary") or ""
    if field_name == "status":
        return _extract_option(fields.get("status"))
    if field_name == "assignee":
        return _extract_option(fields.get("assignee"))
    if field_name == "reporter":
        return _extract_option(fields.get("reporter"))
    if field_name == "priority":
        return _extract_option(fields.get("priority"))
    if field_name == "issue_type":
        return _extract_option(fields.get("issuetype"))
    if field_name in ("created", "updated"):
        return fields.get(field_name) or ""
    if field_name == "labels":
        return ", ".join(fields.get("labels") or [])
    if field_name == "components":
        return ", ".join(_extract_option(c) for c in (fields.get("components") or []))
    if field_name == "fix_versions":
        return ", ".join(_extract_option(v) for v in (fields.get("fixVersions") or []))
    if field_name == "parent":
        parent = fields.get("parent")
        return parent.get("key", "") if parent else ""
    if field_name == "parent_summary":
        parent = fields.get("parent")
        return (parent.get("fields", {}).get("summary", "") if parent else "")

    resolved = _CUSTOM_FIELD_ALIASES.get(field_name, field_name)
    return _extract_option(fields.get(resolved))


class JiraFetcher:
    def __init__(self):
        self._url = None
        self._auth = None
        self._headers = {"Accept": "application/json", "Content-Type": "application/json"}

    def _get_auth(self):
        if self._auth is None:
            url = os.environ.get("JIRA_URL", "https://redhat.atlassian.net")
            token = os.environ.get("JIRA_TOKEN") or os.environ.get("JIRA_API_TOKEN")
            email = os.environ.get("JIRA_EMAIL")
            if not token:
                raise RuntimeError(
                    "JIRA_TOKEN or JIRA_API_TOKEN environment variable is required"
                )
            self._url = url.rstrip("/")
            if email:
                # Atlassian Cloud: basic auth with email + API token
                self._auth = (email, token)
            else:
                # Jira Server/DC: bearer token
                self._headers["Authorization"] = f"Bearer {token}"
                self._auth = False  # sentinel: use header auth
        return self._url, self._auth

    def fetch(self, source: SourceConfig) -> list[dict]:
        if not source.jql:
            return []

        url, auth = self._get_auth()
        endpoint = f"{url}/rest/api/3/search/jql"

        report_fields = source.fields or _DEFAULT_FIELDS
        rest_fields = sorted({_rest_field_id(f) for f in report_fields})

        rows: list[dict] = []
        next_token: str | None = None
        remaining = source.max_results

        while remaining > 0:
            page_size = min(100, remaining)
            body = {
                "jql": source.jql,
                "maxResults": page_size,
                "fields": rest_fields,
            }
            if next_token:
                body["nextPageToken"] = next_token

            kwargs = {"json": body, "headers": self._headers}
            if auth is not False:
                kwargs["auth"] = auth
            resp = requests.post(endpoint, **kwargs)
            resp.raise_for_status()
            data = resp.json()

            issues = data.get("issues", [])
            for issue in issues:
                rows.append({f: _extract_field(issue, f) for f in report_fields})

            remaining -= len(issues)
            next_token = data.get("nextPageToken")
            if data.get("isLast", True) or not next_token or not issues:
                break

        return rows
