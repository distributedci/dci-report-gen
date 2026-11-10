from dci_report_gen.renderers.jinja import (
    _filter_dci_link,
    _filter_find_file,
    _filter_find_testcase,
    _filter_github_run_link,
    _filter_human_duration,
    _filter_regex_extract,
    _filter_short_id,
    _filter_sparkline,
    _filter_stacked_bar_chart_svg,
    _filter_status_emoji,
    _filter_trend_chart_svg,
    _filter_yaml_path,
)


def test_status_emoji_success():
    assert _filter_status_emoji("success") == "✅"


def test_status_emoji_failure():
    assert _filter_status_emoji("failure") == "❌"


def test_status_emoji_running():
    assert _filter_status_emoji("running") == "\U0001f504"


def test_status_emoji_unknown():
    assert _filter_status_emoji("unknown") == "unknown"


def test_dci_link():
    job_id = "255fb46f-ae28-419a-a3ae-7cefbd505087"
    result = _filter_dci_link(job_id)
    assert "[255fb46f]" in result
    assert job_id in result
    assert "/tests)" in result


def test_dci_link_custom_tab():
    job_id = "255fb46f-ae28-419a-a3ae-7cefbd505087"
    result = _filter_dci_link(job_id, tab="files")
    assert "/files)" in result


def test_dci_link_empty():
    assert _filter_dci_link("") == ""
    assert _filter_dci_link(None) == ""


def test_short_id():
    assert _filter_short_id("255fb46f-ae28-419a") == "255fb46f"
    assert _filter_short_id("ab") == "ab"
    assert _filter_short_id("") == ""
    assert _filter_short_id(None) == ""


def test_human_duration_short():
    assert _filter_human_duration(701) == "11m 41s"
    assert _filter_human_duration(87) == "1m 27s"
    assert _filter_human_duration(0) == "0m 00s"


def test_human_duration_long():
    assert _filter_human_duration(701, style="long") == "11 minutes and 41 seconds"


def test_human_duration_invalid():
    assert _filter_human_duration("not_a_number") == "not_a_number"


def test_find_testcase():
    results = [
        {
            "testsuites": [
                {
                    "testcases": [
                        {"name": "deployment_duration", "time": 701},
                        {"name": "policy_reconciliation", "time": 88},
                    ]
                }
            ]
        }
    ]
    tc = _filter_find_testcase(results, "deployment")
    assert tc is not None
    assert tc["time"] == 701

    tc2 = _filter_find_testcase(results, "policy")
    assert tc2["time"] == 88


def test_find_testcase_not_found():
    results = [{"testsuites": [{"testcases": [{"name": "foo"}]}]}]
    assert _filter_find_testcase(results, "bar") is None


def test_find_testcase_empty():
    assert _filter_find_testcase([], "foo") is None
    assert _filter_find_testcase(None, "foo") is None


def test_github_run_link():
    tags = ["daily", "github-29286800312"]
    result = _filter_github_run_link(tags, "rh-telco-labs/blue-slcm-ran-tests")
    assert "29286800312" in result
    assert "actions/runs/29286800312" in result
    assert "[github-29286800312]" in result


def test_github_run_link_no_github_tag():
    tags = ["daily", "ocp"]
    assert _filter_github_run_link(tags, "owner/repo") == ""


def test_github_run_link_empty():
    assert _filter_github_run_link([], "owner/repo") == ""
    assert _filter_github_run_link(None, "owner/repo") == ""


# --- find_file ---


def test_find_file_match():
    files = [
        {"name": "ansible.log", "content": "log data"},
        {"name": "ibi_cluster_timing_site5.txt", "content": "Deployment Duration : 13m"},
    ]
    assert _filter_find_file(files, "ibi_cluster_timing") == "Deployment Duration : 13m"


def test_find_file_no_match():
    files = [{"name": "ansible.log", "content": "log data"}]
    assert _filter_find_file(files, "timing") is None


def test_find_file_empty():
    assert _filter_find_file([], "pattern") is None
    assert _filter_find_file(None, "pattern") is None


# --- regex_extract ---


def test_regex_extract_match():
    text = "Deployment Duration : 13 minutes and 42 seconds"
    assert _filter_regex_extract(text, r"Deployment Duration\s*:\s*(.+)") == "13 minutes and 42 seconds"


def test_regex_extract_with_seconds():
    text = "Reboot : 7m 38s (458s)"
    assert _filter_regex_extract(text, r"Reboot\s*:\s*(\S+\s+\S+)") == "7m 38s"


def test_regex_extract_no_match():
    assert _filter_regex_extract("some text", r"missing:\s*(.+)") is None


def test_regex_extract_empty():
    assert _filter_regex_extract(None, r"(.+)") is None
    assert _filter_regex_extract("", r"(.+)") is None


# --- yaml_path ---


def test_yaml_path_nested():
    text = "spec:\n  seedImageRef:\n    version: '4.20.16'\n"
    assert _filter_yaml_path(text, "spec.seedImageRef.version") == "4.20.16"


def test_yaml_path_simple():
    text = "name: test\nstatus: success\n"
    assert _filter_yaml_path(text, "status") == "success"


def test_yaml_path_missing():
    text = "name: test\n"
    assert _filter_yaml_path(text, "spec.version") is None


def test_yaml_path_empty():
    assert _filter_yaml_path(None, "key") is None
    assert _filter_yaml_path("", "key") is None


def test_yaml_path_invalid_yaml():
    assert _filter_yaml_path("{{invalid", "key") is None


def test_stacked_bar_chart_svg_empty():
    assert _filter_stacked_bar_chart_svg([]) == ""


def test_stacked_bar_chart_svg_basic():
    rows = [
        {"label": "W43", "OpenShift": 100, "RHEL": 20},
        {"label": "W44", "OpenShift": 80, "RHEL": 0},
    ]
    series = [
        {"key": "OpenShift", "color": "#111111"},
        {"key": "RHEL", "label": "RHEL", "color": "#222222"},
    ]
    svg = _filter_stacked_bar_chart_svg(rows, series, title="Jobs by product")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "Jobs by product" in svg
    assert "W43" in svg and "W44" in svg
    # 3 non-zero segments (zero values are skipped) + 2 legend swatches
    assert svg.count('fill="#111111"') == 3
    assert svg.count('fill="#222222"') == 2
    # stacked totals printed above bars
    assert ">120</text>" in svg and ">80</text>" in svg


def test_stacked_bar_chart_svg_infers_series():
    rows = [{"label": "W1", "a": 1, "b": 2}, {"label": "W2", "c": 3}]
    svg = _filter_stacked_bar_chart_svg(rows, show_totals=False)
    for name in ("a", "b", "c"):
        assert f">{name}</text>" in svg


def _viewbox_height(svg: str) -> int:
    import re

    return int(re.search(r'viewBox="0 0 \d+ (\d+)"', svg).group(1))


def test_stacked_bar_chart_svg_default_angle_unchanged():
    rows = [{"label": "W1", "a": 1}]
    svg = _filter_stacked_bar_chart_svg(rows)
    assert "rotate(-35," in svg


def test_stacked_bar_chart_svg_label_angle():
    rows = [{"label": "BM·HA·OVN·dual", "a": 3}]
    svg = _filter_stacked_bar_chart_svg(rows, label_angle=45)
    assert "rotate(-45," in svg
    assert "rotate(-35," not in svg


def test_stacked_bar_chart_svg_horizontal_labels():
    rows = [{"label": "W1", "a": 3}]
    svg = _filter_stacked_bar_chart_svg(rows, label_angle=0)
    assert "rotate(" not in svg
    assert 'text-anchor="middle" fill="#898781">W1<' in svg


def test_stacked_bar_chart_svg_grows_for_long_labels():
    short = _filter_stacked_bar_chart_svg([{"label": "W1", "a": 1}], label_angle=45)
    long_ = _filter_stacked_bar_chart_svg(
        [{"label": "baremetal/highlyavailable/ovnkubernetes/dual", "a": 1}],
        label_angle=45,
    )
    assert _viewbox_height(long_) > _viewbox_height(short)


def test_trend_chart_svg_y_max_and_angle():
    rows = [{"date": "2026-W44", "rate": 50}, {"date": "2026-W45", "rate": 80}]
    svg = _filter_trend_chart_svg(
        rows, title="Success rate", value_key="rate", y_max=100, label_angle=45
    )
    assert ">100<" in svg  # top of Y axis pinned to 100
    assert "rotate(-45," in svg


def test_trend_chart_svg_gaps_for_missing_values():
    rows = [
        {"date": "W1", "rate": 50},
        {"date": "W2", "rate": 60},
        {"date": "W3", "rate": None},
        {"date": "W4", "rate": 70},
        {"date": "W5", "rate": 90},
    ]
    svg = _filter_trend_chart_svg(rows, value_key="rate", y_max=100)
    # two segments of 2 points each -> two polylines, missing point not drawn
    assert svg.count("<polyline") == 2
    assert svg.count('r="3.5"') == 4


def test_sparkline_basic():
    assert _filter_sparkline([0, 50, 100], lo=0, hi=100) == "▁▅█"


def test_sparkline_auto_scale_and_missing():
    assert _filter_sparkline([10, None, 20]) == "▁·█"


def test_sparkline_flat_and_empty():
    assert _filter_sparkline([5, 5]) == "▄▄"
    assert _filter_sparkline([]) == ""
    assert _filter_sparkline([None, None]) == "··"
