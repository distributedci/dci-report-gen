"""Fetcher: point-in-time open-backlog trend from Jira.

Reconstructs how many tickets were *open* on a series of past dates, and how
many of those were *active* (assigned to someone).

For any date ``D``, a ticket counts as "open on D" when it was created on
or before ``D`` and had not been resolved by ``D``:

    <base_jql> AND created <= "D"
              AND (resolutiondate is EMPTY OR resolutiondate > "D")

"Active on D" additionally requires an assignee (count-based approximation:
uses the *current* assignee state, i.e. ``assignee is not EMPTY``):

    <open-on-D JQL> AND assignee is not EMPTY

The ratio ``active / open`` is the share of the open backlog being worked on.

In addition to these point-in-time *stock* counts, the fetcher computes two
per-period *flow* counts, measured over the interval since the previous sample
date ``P`` (exclusive) up to and including the current date ``D``:

    created  = <base_jql> AND created > "P" AND created <= "D"
    closed   = <base_jql> AND resolutiondate > "P" AND resolutiondate <= "D"

For the first sample date there is no previous interval, so ``created`` and
``closed`` are 0 (the opening stock is captured by ``open``).  These flows
satisfy the stock identity ``open(D) ~= open(P) + created - closed``.

Running these counts for each sampled date yields a time series suitable for
charting a backlog trend.  Counts are obtained with the lightweight
``approximate-count`` endpoint (no issue payloads), so the fetcher is cheap
even over long ranges.

Config (``params``):

    base_jql        Base JQL scope, e.g. ``project = CILAB`` (required)
    start_date      First sample date, ISO ``YYYY-MM-DD`` (required)
    end_date        Last sample date, ISO; defaults to today
    granularity     ``weekly`` (default), ``daily``, or ``monthly``

Returns a list of rows ``{"date": "YYYY-MM-DD", "open": <int>,
"active": <int>, "ratio": <float>, "created": <int>, "closed": <int>}``
sorted by ascending date.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import requests

from dci_report_gen.config import SourceConfig


def _parse_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def _sample_dates(start: date, end: date, granularity: str) -> list[date]:
    if start > end:
        return []
    step_days = {"daily": 1, "weekly": 7}.get(granularity)
    dates: list[date] = []
    current = start
    if granularity == "monthly":
        while current <= end:
            dates.append(current)
            # advance one month, clamping the day
            year = current.year + (current.month // 12)
            month = current.month % 12 + 1
            day = min(current.day, 28)
            current = date(year, month, day)
    else:
        if step_days is None:
            raise ValueError(
                f"Unknown granularity: {granularity!r} "
                "(expected daily, weekly, or monthly)"
            )
        while current <= end:
            dates.append(current)
            current += timedelta(days=step_days)
    # Always include the end date as the final sample point.
    if dates and dates[-1] != end:
        dates.append(end)
    return dates


class JiraBacklogTrendFetcher:
    def __init__(self):
        self._url = None
        self._auth = None
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

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
                self._auth = (email, token)
            else:
                self._headers["Authorization"] = f"Bearer {token}"
                self._auth = False  # sentinel: use header auth
        return self._url, self._auth

    def _count(self, jql: str) -> int:
        url, auth = self._get_auth()
        endpoint = f"{url}/rest/api/3/search/approximate-count"
        kwargs = {"json": {"jql": jql}, "headers": self._headers}
        if auth is not False:
            kwargs["auth"] = auth
        resp = requests.post(endpoint, **kwargs)
        resp.raise_for_status()
        return int(resp.json().get("count", 0))

    def fetch(self, source: SourceConfig) -> list[dict]:
        params = source.params or {}
        base_jql = (params.get("base_jql") or "").strip()
        if not base_jql:
            raise ValueError(
                "jira_backlog_trend requires params.base_jql "
                "(e.g. 'project = CILAB')"
            )

        start_raw = params.get("start_date")
        if not start_raw:
            raise ValueError(
                "jira_backlog_trend requires params.start_date (YYYY-MM-DD)"
            )
        start = _parse_date(str(start_raw))

        end_raw = params.get("end_date")
        end = _parse_date(str(end_raw)) if end_raw else date.today()

        granularity = str(params.get("granularity", "weekly")).lower()

        dates = _sample_dates(start, end, granularity)
        rows: list[dict] = []
        total = len(dates)
        prev: date | None = None
        for idx, d in enumerate(dates, 1):
            d_iso = d.isoformat()
            open_jql = (
                f"({base_jql}) "
                f'AND created <= "{d_iso}" '
                f'AND (resolutiondate is EMPTY OR resolutiondate > "{d_iso}")'
            )
            open_count = self._count(open_jql)
            active_jql = f"({open_jql}) AND assignee is not EMPTY"
            active_count = self._count(active_jql)
            ratio = round(active_count / open_count, 3) if open_count else 0.0

            # Per-period flows over (prev, d]; 0 for the first sample.
            if prev is None:
                created_count = 0
                closed_count = 0
            else:
                p_iso = prev.isoformat()
                created_jql = (
                    f"({base_jql}) "
                    f'AND created > "{p_iso}" AND created <= "{d_iso}"'
                )
                created_count = self._count(created_jql)
                closed_jql = (
                    f"({base_jql}) "
                    f'AND resolutiondate > "{p_iso}" '
                    f'AND resolutiondate <= "{d_iso}"'
                )
                closed_count = self._count(closed_jql)

            rows.append(
                {
                    "date": d_iso,
                    "open": open_count,
                    "active": active_count,
                    "ratio": ratio,
                    "created": created_count,
                    "closed": closed_count,
                }
            )
            if idx % 5 == 1 or idx == total:
                print(
                    f"  Jira backlog: {idx}/{total} {d_iso} -> "
                    f"{open_count} open, {active_count} active "
                    f"(ratio {ratio}), +{created_count}/-{closed_count}",
                    file=sys.stderr,
                )
            prev = d

        return rows
