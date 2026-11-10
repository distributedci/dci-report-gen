from unittest.mock import MagicMock, patch

from dci_report_gen.config import SourceConfig
from dci_report_gen.fetchers.dci import DCIFetcher


def _mock_search_response(jobs):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "hits": {
            "hits": [{"_source": job} for job in jobs],
        }
    }
    return resp


@patch("dci_report_gen.fetchers.dci.dci_job")
@patch("dci_report_gen.fetchers.dci._get_context")
def test_include_results_returns_raw_sources(mock_ctx, mock_dci_job):
    mock_ctx.return_value = MagicMock()

    tests_data = [
        {
            "name": "du_latency_001_hwlatdetect",
            "testsuites": [
                {
                    "testcases": [
                        {"name": "hwlatdetect test", "action": "success", "time": 1873},
                    ]
                }
            ],
        }
    ]
    results_data = [{"name": "du_latency_001_hwlatdetect", "failures": 0, "success": 1}]

    jobs = [
        {
            "id": "job-1",
            "name": "latency-tests",
            "status": "success",
            "tests": tests_data,
            "results": results_data,
        },
    ]
    mock_dci_job.search.return_value = _mock_search_response(jobs)

    source = SourceConfig(
        type="dci",
        query="(tags in ['latency'])",
        fields=["id", "name", "status"],
        include_results=True,
    )
    fetcher = DCIFetcher()
    rows = fetcher.fetch(source)

    assert len(rows) == 1
    assert rows[0]["tests"] == tests_data
    assert rows[0]["results"] == results_data
    assert rows[0]["id"] == "job-1"
    assert rows[0]["name"] == "latency-tests"

    call_kwargs = mock_dci_job.search.call_args
    fields_str = call_kwargs.kwargs.get("fields", "") or call_kwargs[1].get("fields", "")
    assert "tests" in fields_str
    assert "results" in fields_str


@patch("dci_report_gen.fetchers.dci.dci_job")
@patch("dci_report_gen.fetchers.dci._get_context")
def test_include_results_false_flattens_rows(mock_ctx, mock_dci_job):
    mock_ctx.return_value = MagicMock()

    jobs = [{"id": "job-1", "name": "ibi", "status": "success", "extra": "data"}]
    mock_dci_job.search.return_value = _mock_search_response(jobs)

    source = SourceConfig(
        type="dci",
        query="(status='success')",
        fields=["id", "name", "status"],
        include_results=False,
    )
    fetcher = DCIFetcher()
    rows = fetcher.fetch(source)

    assert "extra" not in rows[0]
    assert rows[0] == {"id": "job-1", "name": "ibi", "status": "success"}


@patch("dci_report_gen.fetchers.dci.dci_job")
@patch("dci_report_gen.fetchers.dci._get_context")
def test_include_results_preserves_nested_objects(mock_ctx, mock_dci_job):
    mock_ctx.return_value = MagicMock()

    components = [{"type": "ocp", "version": "4.20.16"}, {"type": "rpm", "version": "1.0"}]
    remoteci = {"name": "telco-solutions-ericsson", "id": "abc"}

    jobs = [
        {
            "id": "job-1",
            "name": "ibi",
            "status": "success",
            "components": components,
            "remoteci": remoteci,
            "tests": [],
            "results": [],
        },
    ]
    mock_dci_job.search.return_value = _mock_search_response(jobs)

    source = SourceConfig(
        type="dci",
        query="(tags in ['ibi'])",
        fields=["id", "name", "status", "components", "remoteci"],
        include_results=True,
    )
    fetcher = DCIFetcher()
    rows = fetcher.fetch(source)

    assert rows[0]["components"] == components
    assert rows[0]["remoteci"] == remoteci


@patch("dci_report_gen.fetchers.dci.dci_file")
@patch("dci_report_gen.fetchers.dci.dci_job")
@patch("dci_report_gen.fetchers.dci._get_context")
def test_include_files_downloads_content(mock_ctx, mock_dci_job, mock_dci_file):
    mock_ctx.return_value = MagicMock()

    jobs = [
        {
            "id": "job-1",
            "name": "ibi-deploy",
            "status": "success",
            "files": [
                {"id": "file-1", "name": "ibi_cluster_timing_site5.txt"},
                {"id": "file-2", "name": "ansible.log"},
            ],
        },
    ]
    mock_dci_job.search.return_value = _mock_search_response(jobs)

    content_resp_1 = MagicMock()
    content_resp_1.status_code = 200
    content_resp_1.content = b"Deployment Duration : 13 minutes"

    mock_dci_file.content.side_effect = [content_resp_1]

    source = SourceConfig(
        type="dci",
        query="(name='op-deploy-cloudran-site-ibi')",
        fields=["id", "name", "status", "files.id", "files.name"],
        include_files=True,
        file_patterns=["ibi_cluster_timing"],
    )
    fetcher = DCIFetcher()
    rows = fetcher.fetch(source)

    assert len(rows) == 1
    files = rows[0]["files"]
    assert len(files) == 1
    assert files[0]["name"] == "ibi_cluster_timing_site5.txt"
    assert files[0]["content"] == "Deployment Duration : 13 minutes"


@patch("dci_report_gen.fetchers.dci.dci_file")
@patch("dci_report_gen.fetchers.dci.dci_job")
@patch("dci_report_gen.fetchers.dci._get_context")
def test_include_files_adds_fields_automatically(mock_ctx, mock_dci_job, mock_dci_file):
    mock_ctx.return_value = MagicMock()

    jobs = [{"id": "job-1", "name": "test", "status": "success", "files": []}]
    mock_dci_job.search.return_value = _mock_search_response(jobs)

    source = SourceConfig(
        type="dci",
        query="(status='success')",
        fields=["name", "status"],
        include_files=True,
    )
    fetcher = DCIFetcher()
    fetcher.fetch(source)

    call_kwargs = mock_dci_job.search.call_args
    fields_str = call_kwargs.kwargs.get("fields", "") or call_kwargs[1].get("fields", "")
    assert "files.id" in fields_str
    assert "files.name" in fields_str
    assert "id" in fields_str


def _mock_page(jobs, total):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "hits": {
            "hits": [{"_source": job} for job in jobs],
            "total": {"value": total},
        }
    }
    return resp


@patch("dci_report_gen.fetchers.dci.dci_job")
@patch("dci_report_gen.fetchers.dci._get_context")
def test_search_paginates_beyond_server_page_cap(mock_ctx, mock_dci_job):
    mock_ctx.return_value = MagicMock()
    jobs = [{"id": f"job-{i}"} for i in range(450)]
    mock_dci_job.search.side_effect = [
        _mock_page(jobs[0:200], 450),
        _mock_page(jobs[200:400], 450),
        _mock_page(jobs[400:450], 450),
    ]
    source = SourceConfig(type="dci", query="q", fields=["id"], limit=500)

    rows = DCIFetcher().fetch(source)

    assert [r["id"] for r in rows] == [j["id"] for j in jobs]
    offsets = [c.kwargs["offset"] for c in mock_dci_job.search.call_args_list]
    limits = [c.kwargs["limit"] for c in mock_dci_job.search.call_args_list]
    assert offsets == [0, 200, 400]
    assert limits == [200, 200, 100]


@patch("dci_report_gen.fetchers.dci.dci_job")
@patch("dci_report_gen.fetchers.dci._get_context")
def test_search_respects_limit_across_pages(mock_ctx, mock_dci_job):
    mock_ctx.return_value = MagicMock()
    jobs = [{"id": f"job-{i}"} for i in range(1000)]
    mock_dci_job.search.side_effect = [
        _mock_page(jobs[0:200], 1000),
        _mock_page(jobs[200:250], 1000),
    ]
    source = SourceConfig(type="dci", query="q", fields=["id"], limit=250)

    rows = DCIFetcher().fetch(source)

    assert len(rows) == 250
    assert mock_dci_job.search.call_count == 2


@patch("dci_report_gen.fetchers.dci.dci_job")
@patch("dci_report_gen.fetchers.dci._get_context")
def test_search_with_aggs_is_single_call(mock_ctx, mock_dci_job):
    mock_ctx.return_value = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"hits": {"hits": []}, "aggregations": {"x": {"buckets": []}}}
    mock_dci_job.search.return_value = resp
    source = SourceConfig(
        type="dci", query="q", limit=1, aggs={"x": {"terms": {"field": "status"}}}, aggs_raw=True
    )

    rows = DCIFetcher().fetch(source)

    assert rows == [{"x": {"buckets": []}}]
    assert mock_dci_job.search.call_count == 1
    assert "json-aggs" in mock_dci_job.search.call_args.kwargs
