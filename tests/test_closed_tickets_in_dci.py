from pathlib import Path

from dci_report_gen.config import load_config
from dci_report_gen.engine import _resolve_key_placeholders
from dci_report_gen.renderers.jinja import render_markdown

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_keys_placeholder_with_project():
    fetched = {"jobs": [{"comment": "CILAB-12 and OCPBUGS-3"}]}
    text, had, n = _resolve_key_placeholders("key in ({{keys:jobs}})", fetched, "CILAB")
    assert (text, had, n) == ("key in (CILAB-12)", True, 1)


def test_keys_placeholder_without_project_matches_any_jira_key():
    fetched = {"jobs": [{"tags": ["daily", "SUPPORTEX-25892", "github-867", "OCPBUGS-3"]}]}
    text, had, n = _resolve_key_placeholders("key in ({{keys:jobs}})", fetched, "")
    assert (text, had, n) == ("key in (OCPBUGS-3, SUPPORTEX-25892)", True, 2)


def test_config_loads_and_keeps_keys_placeholders():
    config = load_config(
        str(EXAMPLES / "closed-tickets-in-dci.yaml"),
        {"date_start": "2026-10-01", "date_end": "2026-10-31"},
    )
    assert "created_at>='2026-10-01'" in config.data["workarounds"].query
    assert "created_at<='2026-10-31T23:59:59'" in config.data["test_tickets"].query
    assert "{{keys:workarounds}}" in config.data["closed_workaround_tickets"].jql
    assert "{{keys:test_tickets}}" in config.data["closed_test_tickets"].jql


def _lab(name):
    return {"names": {"buckets": [{"key": name, "doc_count": 1}]}}


def test_template_renders_only_closed_tickets():
    workarounds = [{"by_ticket": {"buckets": [
        {"key": "SUPPORTEX-1", "doc_count": 5,
         "last_seen": {"value_as_string": "2026-10-30T10:00:00.000Z"}, "labs": _lab("lab-a")},
        {"key": "OCPBUGS-2", "doc_count": 3,
         "last_seen": {"value_as_string": "2026-10-29T10:00:00.000Z"}, "labs": _lab("lab-b")},
    ]}}]
    test_tickets = [{"tests": {"suites": {"cases": {"props": {"ticket": {"by_ticket": {"buckets": [
        {"key": "RHEL-7", "doc_count": 4,
         "testcase": {"names": {"buckets": [{"key": "test_ptp", "doc_count": 4}]},
                      "actions": {"buckets": [{"key": "success", "doc_count": 4}]}},
         "job": {"doc_count": 2, "last_seen": {"value_as_string": "2026-10-28T10:00:00.000Z"},
                 "labs": _lab("lab-c")}},
        {"key": "OCPBUGS-9", "doc_count": 1,
         "testcase": {"names": {"buckets": []}, "actions": {"buckets": []}},
         "job": {"doc_count": 1, "labs": _lab("lab-d")}},
    ]}}}}}}}]
    closed = {"summary": "Fixed thing", "status": "Closed", "resolution": "Done",
              "resolutiondate": "2026-09-01T12:00:00.000+0000", "assignee": ""}
    out = render_markdown("closed-tickets-in-dci.md.j2", [EXAMPLES], {
        "title": "T", "date": "2026-11-01", "date_start": "2026-10-01", "date_end": "2026-10-31",
        "workarounds": workarounds,
        "test_tickets": test_tickets,
        "closed_workaround_tickets": [{"key": "SUPPORTEX-1", **closed}],
        "closed_test_tickets": [{"key": "RHEL-7", **closed}],
    })

    assert "[SUPPORTEX-1](" in out and "| 5 | 2026-10-30 | lab-a |" in out
    assert "OCPBUGS-2" not in out
    assert "[RHEL-7](" in out
    assert "| 2026-09-01 | 2 | 2026-10-28 | success: 4 | test_ptp | lab-c |" in out
    assert "OCPBUGS-9" not in out
    assert "| … of which closed in Jira | **1** |" in out
