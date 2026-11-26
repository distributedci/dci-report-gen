"""Read cell values from a Google Sheet with a service account.

Config:

    data:
      pass_rate:
        type: google_sheet
        params:
          spreadsheet_id: 1zunY8PZ...            # from the sheet URL
          ranges: ["'Summary'!C26", "'Summary'!B26:D30"]
          header: false        # optional: first row of each range = column names
          allow_empty: false   # optional: accept ranges without any value

Default mode returns one row per requested range, in request order:
  - ``range``: the range as written in the config
  - ``resolved_range``: the range as normalized by the API (e.g. Summary!C26)
  - ``value``: top-left cell value, or None
  - ``cells``: all cells as a list of rows (trailing empty cells are omitted
    by the API). Not named ``values``: in Jinja ``row.values`` would resolve
    to the ``dict.values`` method instead of the data.

With ``header: true`` each range is read as a table: the first non-blank row
gives the column names (empty header cells become ``colN``), blank rows are
skipped and every other row becomes a dict, plus ``_range``.

Values are unformatted: numeric cells stay numbers (a number formatted as
74.6% is returned as 0.746; a cell holding the text "74.6%" stays a string).
Set ``params.value_render`` to ``FORMATTED_VALUE`` to get the displayed text.

Credentials: ``GOOGLE_SHEETS_CREDENTIALS`` holds the path to a service
account JSON key (or the JSON itself). The spreadsheet must be shared with
the service account email. Requires the ``sheets`` extra:
``pip install 'dci-report-gen[sheets]'``.
"""

from __future__ import annotations

import json
import os

from dci_report_gen.config import SourceConfig

CREDENTIALS_ENV = "GOOGLE_SHEETS_CREDENTIALS"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
API = "https://sheets.googleapis.com/v4/spreadsheets/{}/values:batchGet"
_RENDER_OPTIONS = {"UNFORMATTED_VALUE", "FORMATTED_VALUE", "FORMULA"}


def _authorized_session():
    """Build a requests session authenticated with the service account."""
    raw = os.environ.get(CREDENTIALS_ENV, "").strip()
    if not raw:
        raise RuntimeError(
            f"google_sheet source requires {CREDENTIALS_ENV} "
            "(path to a service account JSON key, or the JSON itself)"
        )
    try:
        # Imported lazily: only configs with a google_sheet source need them.
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError(
            "google_sheet source requires google-auth: "
            "pip install 'dci-report-gen[sheets]'"
        ) from exc
    if raw.startswith("{"):
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=SCOPES
        )
    else:
        credentials = service_account.Credentials.from_service_account_file(
            os.path.expanduser(raw), scopes=SCOPES
        )
    return AuthorizedSession(credentials)


def _http_error(response, spreadsheet_id: str) -> RuntimeError:
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = response.text[:200]
    hint = {
        403: "is the spreadsheet shared with the service account email?",
        404: "check params.spreadsheet_id",
        400: "check params.ranges (A1 notation, quote tab names: 'My tab'!A1)",
    }.get(response.status_code, "")
    return RuntimeError(
        f"Google Sheets API returned HTTP {response.status_code} for spreadsheet "
        f"{spreadsheet_id}: {message}" + (f" ({hint})" if hint else "")
    )


def _is_blank(cells: list) -> bool:
    return not any(str(c).strip() for c in cells if c is not None)


def _table_rows(requested: str, values: list[list]) -> list[dict]:
    """Turn a range into dicts: first non-blank row = headers, blank rows skipped."""
    non_blank = [cells for cells in values if not _is_blank(cells)]
    if not non_blank:
        return []
    # Empty header cells get a positional name so columns never collide.
    headers = [str(h).strip() or f"col{i + 1}" for i, h in enumerate(non_blank[0])]
    rows = []
    for cells in non_blank[1:]:
        cells = list(cells) + [None] * (len(headers) - len(cells))
        row = dict(zip(headers, cells))
        row["_range"] = requested
        rows.append(row)
    return rows


class GoogleSheetFetcher:
    def __init__(self, session=None):
        # A session can be injected (tests); otherwise built on first fetch.
        self._session = session

    def fetch(self, source: SourceConfig) -> list[dict]:
        params = source.params or {}
        spreadsheet_id = params.get("spreadsheet_id")
        if not isinstance(spreadsheet_id, str) or not spreadsheet_id.strip():
            raise ValueError("google_sheet source requires params.spreadsheet_id")
        ranges = params.get("ranges")
        if isinstance(ranges, str):
            ranges = [ranges]
        if not ranges or not all(isinstance(r, str) and r.strip() for r in ranges):
            raise ValueError(
                "google_sheet source requires params.ranges (list of A1 ranges)"
            )
        render = params.get("value_render", "UNFORMATTED_VALUE")
        if render not in _RENDER_OPTIONS:
            raise ValueError(
                f"params.value_render must be one of {', '.join(sorted(_RENDER_OPTIONS))}"
            )

        if self._session is None:
            self._session = _authorized_session()
        response = self._session.get(
            API.format(spreadsheet_id),
            params={"ranges": ranges, "valueRenderOption": render},
            timeout=30,
        )
        if response.status_code != 200:
            raise _http_error(response, spreadsheet_id)
        value_ranges = response.json().get("valueRanges", [])
        if len(value_ranges) != len(ranges):
            raise RuntimeError(
                f"Google Sheets API returned {len(value_ranges)} ranges, "
                f"expected {len(ranges)}"
            )

        # Ranges come back in request order; empty ranges have no "values".
        empty = [
            r for r, vr in zip(ranges, value_ranges) if not any(vr.get("values") or [])
        ]
        if empty and not params.get("allow_empty", False):
            raise ValueError(
                f"google_sheet: no value in {', '.join(empty)} of spreadsheet "
                f"{spreadsheet_id} (rows may have moved; set params.allow_empty "
                "to accept empty ranges)"
            )

        rows: list[dict] = []
        for requested, value_range in zip(ranges, value_ranges):
            values = value_range.get("values") or []
            if params.get("header", False):
                rows.extend(_table_rows(requested, values))
                continue
            first = values[0][0] if values and values[0] else None
            rows.append(
                {
                    "range": requested,
                    "resolved_range": value_range.get("range", requested),
                    "value": first,
                    "cells": values,
                }
            )
        return rows
