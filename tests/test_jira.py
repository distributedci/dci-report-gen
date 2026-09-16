from unittest.mock import MagicMock, patch

import pytest

from dci_report_gen.config import SourceConfig
from dci_report_gen.fetchers.jira import (
    JiraFetcher,
    _extract_field,
    _extract_option,
    _rest_field_id,
)


def _mock_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _issue(**fields):
    key = fields.pop("key", "PROJ-1")
    return {"key": key, "fields": fields}


# --- _rest_field_id -------------------------------------------------------


def test_rest_field_id_maps_report_names():
    assert _rest_field_id("issue_type") == "issuetype"
    assert _rest_field_id("fix_versions") == "fixVersions"
    assert _rest_field_id("parent_summary") == "parent"


def test_rest_field_id_resolves_custom_field_alias():
    assert _rest_field_id("severity") == "customfield_10840"
    assert _rest_field_id("story_points") == "customfield_10028"


def test_rest_field_id_passes_through_unknown_name():
    assert _rest_field_id("customfield_99999") == "customfield_99999"


# --- _extract_option ------------------------------------------------------


def test_extract_option_none_returns_empty():
    assert _extract_option(None) == ""


def test_extract_option_dict_prefers_value_then_name():
    assert _extract_option({"value": "High", "name": "ignored"}) == "High"
    assert _extract_option({"name": "In Progress"}) == "In Progress"
    assert _extract_option({"displayName": "Jane Doe"}) == "Jane Doe"


def test_extract_option_dict_without_known_keys_returns_empty():
    assert _extract_option({"id": "10001"}) == ""


def test_extract_option_list_joins_items():
    value = [{"value": "a"}, {"name": "b"}, "c"]
    assert _extract_option(value) == "a, b, c"


def test_extract_option_scalar_stringified():
    assert _extract_option(5) == "5"


# --- _extract_field -------------------------------------------------------


def test_extract_field_key_and_summary():
    issue = _issue(key="PROJ-42", summary="Fix the thing")
    assert _extract_field(issue, "key") == "PROJ-42"
    assert _extract_field(issue, "summary") == "Fix the thing"


def test_extract_field_option_fields():
    issue = _issue(
        status={"name": "Done"},
        assignee={"displayName": "Jane Doe"},
        priority={"name": "High"},
        issuetype={"name": "Bug"},
    )
    assert _extract_field(issue, "status") == "Done"
    assert _extract_field(issue, "assignee") == "Jane Doe"
    assert _extract_field(issue, "priority") == "High"
    assert _extract_field(issue, "issue_type") == "Bug"


def test_extract_field_missing_assignee_returns_empty():
    issue = _issue(assignee=None)
    assert _extract_field(issue, "assignee") == ""


def test_extract_field_labels_and_lists():
    issue = _issue(
        labels=["telco", "ci"],
        components=[{"name": "networking"}, {"name": "storage"}],
        fixVersions=[{"name": "4.20"}],
    )
    assert _extract_field(issue, "labels") == "telco, ci"
    assert _extract_field(issue, "components") == "networking, storage"
    assert _extract_field(issue, "fix_versions") == "4.20"


def test_extract_field_empty_lists_return_empty_string():
    issue = _issue(labels=[], components=[], fixVersions=[])
    assert _extract_field(issue, "labels") == ""
    assert _extract_field(issue, "components") == ""
    assert _extract_field(issue, "fix_versions") == ""


def test_extract_field_parent_and_parent_summary():
    issue = _issue(
        parent={"key": "PROJ-1", "fields": {"summary": "Epic title"}},
    )
    assert _extract_field(issue, "parent") == "PROJ-1"
    assert _extract_field(issue, "parent_summary") == "Epic title"


def test_extract_field_missing_parent_returns_empty():
    issue = _issue()
    assert _extract_field(issue, "parent") == ""
    assert _extract_field(issue, "parent_summary") == ""


def test_extract_field_custom_field_alias():
    issue = _issue(customfield_10840={"value": "Critical"})
    assert _extract_field(issue, "severity") == "Critical"


def test_extract_field_unknown_field_returns_empty():
    issue = _issue()
    assert _extract_field(issue, "does_not_exist") == ""


# --- JiraFetcher.fetch ----------------------------------------------------


def test_fetch_no_jql_returns_empty():
    fetcher = JiraFetcher()
    assert fetcher.fetch(SourceConfig(type="jira")) == []


@patch.dict("os.environ", {"JIRA_URL": "https://jira.example.com", "JIRA_TOKEN": "tok"})
@patch("dci_report_gen.fetchers.jira.requests")
def test_fetch_maps_fields_to_rows(mock_requests):
    payload = {
        "issues": [
            _issue(key="PROJ-1", summary="First", status={"name": "Open"}),
            _issue(key="PROJ-2", summary="Second", status={"name": "Done"}),
        ],
        "isLast": True,
    }
    mock_requests.post.return_value = _mock_response(payload)

    source = SourceConfig(type="jira", jql="project = PROJ", fields=["key", "summary", "status"])
    rows = JiraFetcher().fetch(source)

    assert rows == [
        {"key": "PROJ-1", "summary": "First", "status": "Open"},
        {"key": "PROJ-2", "summary": "Second", "status": "Done"},
    ]


@patch.dict("os.environ", {"JIRA_URL": "https://jira.example.com", "JIRA_TOKEN": "tok"})
@patch("dci_report_gen.fetchers.jira.requests")
def test_fetch_requests_resolved_rest_fields(mock_requests):
    mock_requests.post.return_value = _mock_response({"issues": [], "isLast": True})

    source = SourceConfig(type="jira", jql="x", fields=["key", "issue_type", "severity"])
    JiraFetcher().fetch(source)

    body = mock_requests.post.call_args.kwargs["json"]
    assert body["jql"] == "x"
    assert set(body["fields"]) == {"key", "issuetype", "customfield_10840"}


@patch.dict("os.environ", {"JIRA_URL": "https://jira.example.com", "JIRA_TOKEN": "tok"})
@patch("dci_report_gen.fetchers.jira.requests")
def test_fetch_uses_default_fields_when_unset(mock_requests):
    mock_requests.post.return_value = _mock_response(
        {"issues": [_issue(key="PROJ-1")], "isLast": True}
    )

    rows = JiraFetcher().fetch(SourceConfig(type="jira", jql="x"))

    body = mock_requests.post.call_args.kwargs["json"]
    assert set(body["fields"]) == {"key", "summary", "status", "assignee"}
    assert set(rows[0]) == {"key", "summary", "status", "assignee"}


@patch.dict("os.environ", {"JIRA_URL": "https://jira.example.com", "JIRA_TOKEN": "tok"})
@patch("dci_report_gen.fetchers.jira.requests")
def test_fetch_paginates_with_next_token(mock_requests):
    page1 = {
        "issues": [_issue(key=f"PROJ-{i}") for i in range(100)],
        "nextPageToken": "tok2",
        "isLast": False,
    }
    page2 = {
        "issues": [_issue(key="PROJ-100")],
        "isLast": True,
    }
    mock_requests.post.side_effect = [_mock_response(page1), _mock_response(page2)]

    source = SourceConfig(type="jira", jql="x", fields=["key"], max_results=150)
    rows = JiraFetcher().fetch(source)

    assert len(rows) == 101
    assert mock_requests.post.call_count == 2
    second_body = mock_requests.post.call_args_list[1].kwargs["json"]
    assert second_body["nextPageToken"] == "tok2"


@patch.dict("os.environ", {"JIRA_URL": "https://jira.example.com", "JIRA_TOKEN": "tok"})
@patch("dci_report_gen.fetchers.jira.requests")
def test_fetch_stops_at_max_results(mock_requests):
    mock_requests.post.return_value = _mock_response(
        {"issues": [_issue(key=f"PROJ-{i}") for i in range(10)], "isLast": True}
    )

    source = SourceConfig(type="jira", jql="x", fields=["key"], max_results=10)
    JiraFetcher().fetch(source)

    body = mock_requests.post.call_args.kwargs["json"]
    assert body["maxResults"] == 10


@patch.dict(
    "os.environ",
    {"JIRA_URL": "https://jira.example.com", "JIRA_TOKEN": "tok", "JIRA_EMAIL": "me@example.com"},
)
@patch("dci_report_gen.fetchers.jira.requests")
def test_fetch_cloud_uses_basic_auth(mock_requests):
    mock_requests.post.return_value = _mock_response({"issues": [], "isLast": True})

    JiraFetcher().fetch(SourceConfig(type="jira", jql="x"))

    kwargs = mock_requests.post.call_args.kwargs
    assert kwargs["auth"] == ("me@example.com", "tok")
    assert "Authorization" not in kwargs["headers"]


@patch.dict(
    "os.environ",
    {"JIRA_URL": "https://jira.example.com/", "JIRA_TOKEN": "tok", "JIRA_EMAIL": ""},
)
@patch("dci_report_gen.fetchers.jira.requests")
def test_fetch_server_uses_bearer_auth(mock_requests):
    mock_requests.post.return_value = _mock_response({"issues": [], "isLast": True})

    JiraFetcher().fetch(SourceConfig(type="jira", jql="x"))

    kwargs = mock_requests.post.call_args.kwargs
    assert "auth" not in kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    # trailing slash stripped from the endpoint
    assert mock_requests.post.call_args.args[0] == (
        "https://jira.example.com/rest/api/3/search/jql"
    )


@patch.dict("os.environ", {}, clear=True)
@patch("dci_report_gen.fetchers.jira.requests")
def test_fetch_missing_token_raises(mock_requests):
    with pytest.raises(RuntimeError, match="JIRA_TOKEN"):
        JiraFetcher().fetch(SourceConfig(type="jira", jql="x"))
