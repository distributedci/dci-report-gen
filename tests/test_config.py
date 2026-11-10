import os
import re
from datetime import date, timedelta
from unittest.mock import patch

from dci_report_gen.config import (
    SourceConfig,
    _apply_vars_to_source,
    _resolve_date_expr,
    _resolve_vars,
    _substitute_vars_expr,
    load_config,
)


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_load_config():
    path = os.path.join(FIXTURES, "sample_config.yaml")
    config = load_config(path)

    assert config.title == "Test Report"
    assert config.author == "Test Author"
    assert config.date == "2024-06-01"
    assert len(config.sections) == 1

    section = config.sections[0]
    assert section.name == "OCP Jobs"
    assert section.source.type == "dci"
    assert "2024-06-01" in section.source.query
    assert section.render.style == "table"
    assert len(section.render.columns) == 3


def test_var_substitution():
    path = os.path.join(FIXTURES, "sample_config.yaml")
    config = load_config(path, var_overrides={"date_start": "2024-07-01"})

    assert "2024-07-01" in config.sections[0].source.query
    assert "2024-06-01" not in config.sections[0].source.query


def test_auto_date():
    path = os.path.join(FIXTURES, "sample_config.yaml")
    config = load_config(path)
    assert config.date == "2024-06-01"


def test_include_results_config():
    path = os.path.join(FIXTURES, "config_with_results.yaml")
    config = load_config(path)

    assert config.data is not None
    assert config.data["jobs"].include_results is True


def test_context_config():
    path = os.path.join(FIXTURES, "config_with_results.yaml")
    config = load_config(path)

    assert config.context is not None
    assert config.context["site_hardware"]["site5"] == "HPE DL110"
    assert config.context["site_hardware"]["site10"] == "Dell SPR-EE"


def test_default_include_results_false():
    path = os.path.join(FIXTURES, "sample_config.yaml")
    config = load_config(path)

    for section in config.sections:
        assert section.source.include_results is False


def test_default_context_empty():
    path = os.path.join(FIXTURES, "sample_config.yaml")
    config = load_config(path)

    assert config.context == {}


def test_include_files_config():
    path = os.path.join(FIXTURES, "config_with_results.yaml")
    config = load_config(path)

    assert config.data["jobs_with_files"].include_files is True
    assert config.data["jobs_with_files"].file_patterns == ["ibi_cluster_timing", "microcode_"]
    assert config.data["jobs"].include_files is False
    assert config.data["jobs"].file_patterns is None


# ── Date expression tests ──────────────────────────────────────────


class TestResolveDateExpr:
    @patch("dci_report_gen.config.date")
    def test_today(self, mock_date):
        mock_date.today.return_value = date(2026, 9, 9)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        assert _resolve_date_expr("{today}") == "2026-09-09"

    @patch("dci_report_gen.config.date")
    def test_today_minus_days(self, mock_date):
        mock_date.today.return_value = date(2026, 9, 9)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        assert _resolve_date_expr("{today-7d}") == "2026-09-02"

    @patch("dci_report_gen.config.date")
    def test_today_plus_days(self, mock_date):
        mock_date.today.return_value = date(2026, 9, 9)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        assert _resolve_date_expr("{today+3d}") == "2026-09-12"

    def test_no_expression(self):
        assert _resolve_date_expr("plain text") == "plain text"
        assert _resolve_date_expr("2026-01-01") == "2026-01-01"

    @patch("dci_report_gen.config.date")
    def test_embedded_in_text(self, mock_date):
        mock_date.today.return_value = date(2026, 9, 9)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        assert _resolve_date_expr("from {today-7d} to {today}") == "from 2026-09-02 to 2026-09-09"


class TestSubstituteVarsExpr:
    def test_simple_ref(self):
        assert _substitute_vars_expr("{{days}}", {"days": "7"}) == "7"

    def test_multiply(self):
        assert _substitute_vars_expr("{{days*2}}", {"days": "7"}) == "14"

    def test_add(self):
        assert _substitute_vars_expr("{{days+3}}", {"days": "7"}) == "10"

    def test_subtract(self):
        assert _substitute_vars_expr("{{days-2}}", {"days": "7"}) == "5"

    def test_in_date_expr(self):
        result = _substitute_vars_expr("{today-{{days}}d}", {"days": "7"})
        assert result == "{today-7d}"

    def test_arithmetic_in_date_expr(self):
        result = _substitute_vars_expr("{today-{{days*2}}d}", {"days": "7"})
        assert result == "{today-14d}"


class TestResolveVars:
    @patch("dci_report_gen.config.date")
    def test_full_pipeline(self, mock_date):
        mock_date.today.return_value = date(2026, 9, 9)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        vars = {
            "days": "7",
            "date_end": "{today}",
            "date_start": "{today-{{days}}d}",
            "prev_start": "{today-{{days*2}}d}",
        }
        result = _resolve_vars(vars)
        assert result["days"] == "7"
        assert result["date_end"] == "2026-09-09"
        assert result["date_start"] == "2026-09-02"
        assert result["prev_start"] == "2026-08-26"

    @patch("dci_report_gen.config.date")
    def test_override_days(self, mock_date):
        mock_date.today.return_value = date(2026, 9, 9)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        vars = {
            "days": "5",
            "date_end": "{today}",
            "date_start": "{today-{{days}}d}",
            "prev_start": "{today-{{days*2}}d}",
        }
        result = _resolve_vars(vars)
        assert result["date_start"] == "2026-09-04"
        assert result["prev_start"] == "2026-08-30"

    def test_plain_vars_unchanged(self):
        vars = {"date_start": "2024-06-01", "name": "test"}
        result = _resolve_vars(vars)
        assert result == {"date_start": "2024-06-01", "name": "test"}


def test_vars_substituted_in_aggs():
    src = SourceConfig(
        type="dci",
        query="created_at>='{{trend_start}}'",
        aggs={
            "week": {
                "date_histogram": {
                    "field": "created_at",
                    "calendar_interval": "week",
                    "extended_bounds": {"min": "{{trend_start}}", "max": "{{ date_end }}"},
                },
                "aggs": {"st": {"terms": {"field": "status", "size": 10}}},
            },
            "plat": {"terms": {"field": "tags", "include": ["a", "{{trend_start}}"]}},
        },
    )
    _apply_vars_to_source(src, {"trend_start": "2026-08-31", "date_end": "2026-11-11"})
    bounds = src.aggs["week"]["date_histogram"]["extended_bounds"]
    assert bounds == {"min": "2026-08-31", "max": "2026-11-11"}
    assert src.aggs["week"]["aggs"]["st"]["terms"]["size"] == 10  # non-str untouched
    assert src.aggs["plat"]["terms"]["include"] == ["a", "2026-08-31"]
