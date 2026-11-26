import pytest

from dci_report_gen.config import SourceConfig
from dci_report_gen.engine import ReportEngine
from dci_report_gen.fetchers import google_sheet
from dci_report_gen.fetchers.google_sheet import GoogleSheetFetcher

SHEET_ID = "1zunY8PZabcdefghijklmnopqrstuvwxyz"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.response


def source(**params):
    return SourceConfig(
        type="google_sheet", params={"spreadsheet_id": SHEET_ID, **params}
    )


def test_cells_in_request_order():
    session = FakeSession(
        FakeResponse(
            payload={
                "valueRanges": [
                    {"range": "Summary!C26", "values": [[0.746]]},
                    {
                        "range": "Summary!B26:C27",
                        "values": [["Pass rate", 0.7], ["Other"]],
                    },
                ]
            }
        )
    )
    rows = GoogleSheetFetcher(session).fetch(
        source(ranges=["'Summary'!C26", "'Summary'!B26:C27"])
    )
    assert rows == [
        {
            "range": "'Summary'!C26",
            "resolved_range": "Summary!C26",
            "value": 0.746,
            "values": [[0.746]],
        },
        {
            "range": "'Summary'!B26:C27",
            "resolved_range": "Summary!B26:C27",
            "value": "Pass rate",
            "values": [["Pass rate", 0.7], ["Other"]],
        },
    ]
    url, params = session.calls[0]
    assert url.endswith(f"/spreadsheets/{SHEET_ID}/values:batchGet")
    assert params == {
        "ranges": ["'Summary'!C26", "'Summary'!B26:C27"],
        "valueRenderOption": "UNFORMATTED_VALUE",
    }


def test_single_range_string_and_value_render():
    session = FakeSession(
        FakeResponse(
            payload={
                "valueRanges": [
                    {"range": "Summary!C26", "values": [["74.6%"]]},
                ]
            }
        )
    )
    rows = GoogleSheetFetcher(session).fetch(
        source(ranges="Summary!C26", value_render="FORMATTED_VALUE")
    )
    assert rows[0]["value"] == "74.6%"
    assert session.calls[0][1]["valueRenderOption"] == "FORMATTED_VALUE"


def test_header_mode_builds_dicts():
    session = FakeSession(
        FakeResponse(
            payload={
                "valueRanges": [
                    {
                        "range": "Data!A1:C3",
                        "values": [
                            ["Partner", "Jobs", "Pass"],
                            ["Nokia", 12, 0.9],
                            ["F5"],
                        ],
                    },
                ]
            }
        )
    )
    rows = GoogleSheetFetcher(session).fetch(source(ranges=["Data!A1:C3"], header=True))
    assert rows == [
        {"Partner": "Nokia", "Jobs": 12, "Pass": 0.9, "_range": "Data!A1:C3"},
        {"Partner": "F5", "Jobs": None, "Pass": None, "_range": "Data!A1:C3"},
    ]


def test_empty_range_fails_unless_allowed():
    payload = {
        "valueRanges": [
            {"range": "Summary!C26", "values": [[1]]},
            {"range": "Summary!C99"},
        ]
    }
    with pytest.raises(ValueError, match="no value in Summary!C99"):
        GoogleSheetFetcher(FakeSession(FakeResponse(payload=payload))).fetch(
            source(ranges=["Summary!C26", "Summary!C99"])
        )
    rows = GoogleSheetFetcher(FakeSession(FakeResponse(payload=payload))).fetch(
        source(ranges=["Summary!C26", "Summary!C99"], allow_empty=True)
    )
    assert rows[1]["value"] is None and rows[1]["values"] == []


def test_http_403_explains_sharing():
    response = FakeResponse(
        403, {"error": {"message": "The caller does not have permission"}}
    )
    with pytest.raises(RuntimeError, match="HTTP 403.*shared with the service account"):
        GoogleSheetFetcher(FakeSession(response)).fetch(source(ranges=["A1"]))


def test_range_count_mismatch_fails():
    session = FakeSession(FakeResponse(payload={"valueRanges": []}))
    with pytest.raises(RuntimeError, match="returned 0 ranges, expected 1"):
        GoogleSheetFetcher(session).fetch(source(ranges=["A1"]))


@pytest.mark.parametrize(
    "params, message",
    [
        ({"ranges": ["A1"]}, "spreadsheet_id"),
        ({"spreadsheet_id": SHEET_ID}, "ranges"),
        (
            {"spreadsheet_id": SHEET_ID, "ranges": ["A1"], "value_render": "RAW"},
            "value_render",
        ),
    ],
)
def test_invalid_params(params, message):
    session = FakeSession(FakeResponse(payload={"valueRanges": []}))
    with pytest.raises(ValueError, match=message):
        GoogleSheetFetcher(session).fetch(
            SourceConfig(type="google_sheet", params=params)
        )
    assert session.calls == []


def test_missing_credentials(monkeypatch):
    monkeypatch.delenv(google_sheet.CREDENTIALS_ENV, raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_SHEETS_CREDENTIALS"):
        GoogleSheetFetcher().fetch(source(ranges=["A1"]))


def test_engine_routes_google_sheet():
    assert isinstance(ReportEngine()._get_fetcher("google_sheet"), GoogleSheetFetcher)
