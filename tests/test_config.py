import datetime
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from dci_report_gen.config import (
    MissingVariablesError,
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


def _mock_today(mock_dt, fixed_date: datetime.date) -> None:
    """Configure mock_dt (patched datetime module) to return fixed_date for now().date()."""
    mock_dt.datetime.now.return_value.date.return_value = fixed_date
    mock_dt.timedelta = datetime.timedelta
    mock_dt.timezone = datetime.timezone


class TestResolveDateExpr:
    @patch("dci_report_gen.config.datetime")
    def test_today(self, mock_dt):
        _mock_today(mock_dt, datetime.date(2026, 9, 9))
        assert _resolve_date_expr("{today}") == "2026-09-09"

    @patch("dci_report_gen.config.datetime")
    def test_today_minus_days(self, mock_dt):
        _mock_today(mock_dt, datetime.date(2026, 9, 9))
        assert _resolve_date_expr("{today-7d}") == "2026-09-02"

    @patch("dci_report_gen.config.datetime")
    def test_today_plus_days(self, mock_dt):
        _mock_today(mock_dt, datetime.date(2026, 9, 9))
        assert _resolve_date_expr("{today+3d}") == "2026-09-12"

    def test_no_expression(self):
        assert _resolve_date_expr("plain text") == "plain text"
        assert _resolve_date_expr("2026-01-01") == "2026-01-01"

    @patch("dci_report_gen.config.datetime")
    def test_embedded_in_text(self, mock_dt):
        _mock_today(mock_dt, datetime.date(2026, 9, 9))
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
    @patch("dci_report_gen.config.datetime")
    def test_full_pipeline(self, mock_dt):
        _mock_today(mock_dt, datetime.date(2026, 9, 9))
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

    @patch("dci_report_gen.config.datetime")
    def test_override_days(self, mock_dt):
        _mock_today(mock_dt, datetime.date(2026, 9, 9))
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


# ── Pre-flight validation tests ────────────────────────────────────


class TestPreflightValidation:
    def _write_config(self, tmp_path: Path, content: str) -> str:
        cfg = tmp_path / "report.yaml"
        cfg.write_text(content)
        return str(cfg)

    def test_missing_config_var_raises_clean_error(self, tmp_path):
        """A single undefined {{var}} in a data query raises MissingVariablesError."""
        cfg = self._write_config(
            tmp_path,
            """
report:
  title: "Test"
sections:
  - name: "Jobs"
    source:
      type: dci
      query: "created_at>='{{date_start}}'"
    render:
      style: table
      columns:
        - header: ID
          field: id
""",
        )
        with pytest.raises(MissingVariablesError) as exc_info:
            load_config(cfg)
        assert "date_start" in exc_info.value.missing

    def test_multiple_missing_vars_reported_at_once(self, tmp_path):
        """Multiple undefined vars are all reported in a single raise."""
        cfg = self._write_config(
            tmp_path,
            """
report:
  title: "Test"
sections:
  - name: "Jobs"
    source:
      type: dci
      query: "created_at>='{{date_start}}' and site='{{site}}'"
    render:
      style: table
      columns:
        - header: ID
          field: id
""",
        )
        with pytest.raises(MissingVariablesError) as exc_info:
            load_config(cfg)
        assert "date_start" in exc_info.value.missing
        assert "site" in exc_info.value.missing

    def test_all_vars_supplied_no_error(self, tmp_path):
        """When all {{var}} references are covered by the vars block, no error is raised."""
        cfg = self._write_config(
            tmp_path,
            """
report:
  title: "Test"
vars:
  date_start: "2024-06-01"
sections:
  - name: "Jobs"
    source:
      type: dci
      query: "created_at>='{{date_start}}'"
    render:
      style: table
      columns:
        - header: ID
          field: id
""",
        )
        # Should not raise
        config = load_config(cfg)
        assert config.title == "Test"

    def test_missing_jinja2_template_var(self, tmp_path):
        """A Jinja2 template variable not in context/vars/data raises MissingVariablesError."""
        # Create a minimal Jinja2 template that references an undefined variable
        template_file = tmp_path / "report.md.j2"
        template_file.write_text("# {{ title }}\n{{ missing_var }}\n")

        cfg = self._write_config(
            tmp_path,
            """
report:
  title: "Test"
  layout: "report.md.j2"
data:
  jobs:
    type: dci
    query: "status='success'"
""",
        )
        with pytest.raises(MissingVariablesError) as exc_info:
            load_config(cfg)
        assert "missing_var" in exc_info.value.missing

    def test_jinja2_loop_vars_not_flagged(self, tmp_path):
        """Loop variables in Jinja2 templates are not reported as missing."""
        # Create a template with a for-loop — 'j' is a loop var, not a missing var
        template_file = tmp_path / "report.md.j2"
        template_file.write_text(
            "# {{ title }}\n{% for j in jobs %}{{ j.id }}{% endfor %}\n"
        )

        cfg = self._write_config(
            tmp_path,
            """
report:
  title: "Test"
  layout: "report.md.j2"
data:
  jobs:
    type: dci
    query: "status='success'"
""",
        )
        # 'jobs' is in data, 'j' is a loop var — should not raise
        config = load_config(cfg)
        assert config.title == "Test"

    def test_missing_var_error_contains_hint(self, tmp_path):
        """The error message includes a --var KEY=VALUE hint for each missing variable."""
        cfg = self._write_config(
            tmp_path,
            """
report:
  title: "Test"
sections:
  - name: "Jobs"
    source:
      type: dci
      query: "created_at>='{{my_date}}'"
    render:
      style: table
      columns:
        - header: ID
          field: id
""",
        )
        with pytest.raises(MissingVariablesError) as exc_info:
            load_config(cfg)
        error_str = str(exc_info.value)
        assert "--var my_date=VALUE" in error_str
