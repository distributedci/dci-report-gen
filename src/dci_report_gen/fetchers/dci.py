from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from dciclient.v1.api import context as dci_context
from dciclient.v1.api import file as dci_file
from dciclient.v1.api import job as dci_job

from dci_report_gen.config import SourceConfig


def _get_context():
    return dci_context.build_signature_context()


def _extract_field(obj: dict, dotted_key: str):
    parts = dotted_key.split(".")
    current = obj
    for i, part in enumerate(parts):
        if current is None:
            return None
        if isinstance(current, list):
            return ", ".join(
                str(_extract_field(item, ".".join(parts[i:])))
                for item in current
                if item is not None
            )
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _flatten_row(hit: dict, fields: list[str] | None) -> dict:
    if fields:
        return {f: _extract_field(hit, f) for f in fields}
    return hit


# DCI server caps a single search page at 200 results.
DCI_MAX_PAGE_SIZE = 200


def _hits_and_total(data: dict) -> tuple[list[dict], int | None]:
    hits_obj = data.get("hits", {})
    if not isinstance(hits_obj, dict):
        return [h.get("_source", h) for h in hits_obj], None
    hits = [h.get("_source", h) for h in hits_obj.get("hits", [])]
    total = hits_obj.get("total")
    if isinstance(total, dict):
        total = total.get("value")
    return hits, int(total) if total is not None else None


def _paginated_search(ctx, params: dict, max_results: int) -> list[dict]:
    """Fetch up to max_results jobs, paginating with offset/limit since the
    server caps each page at DCI_MAX_PAGE_SIZE results."""
    page_size = max(1, min(DCI_MAX_PAGE_SIZE, max_results))
    offset = 0
    results: list[dict] = []
    while len(results) < max_results:
        limit = min(page_size, max_results - len(results))
        resp = dci_job.search(ctx, **params, limit=limit, offset=offset)
        if resp.status_code != 200:
            raise RuntimeError(f"DCI search failed ({resp.status_code}): {resp.text}")
        page, total = _hits_and_total(resp.json())
        results.extend(page)
        if total is not None and total > len(page):
            print(
                f"    … fetched {len(results)}/{min(total, max_results)} jobs (offset={offset})",
                file=sys.stderr,
            )
        if len(page) < limit or (total is not None and offset + len(page) >= total):
            break
        offset += len(page)
    return results[:max_results]


class DCIFetcher:
    def fetch(self, source: SourceConfig) -> list[dict]:
        ctx = _get_context()

        if source.transform == "weekly_file_coverage":
            return self._transform_weekly_file_coverage(ctx, source)
        if source.query:
            return self._search_jobs(ctx, source)
        return []

    def _search_jobs(self, ctx, source: SourceConfig) -> list[dict]:
        fields = list(source.fields) if source.fields else None
        if source.include_results:
            if fields is None:
                fields = []
            for f in ("tests", "results", "id"):
                if f not in fields:
                    fields.append(f)
        if source.include_files:
            if fields is None:
                fields = []
            for f in ("files.id", "files.name", "id"):
                if f not in fields:
                    fields.append(f)

        import json as _json

        params = {"query": source.query, "sort": source.sort}
        if fields:
            params["fields"] = ",".join(fields)

        if source.aggs:
            # DCI server requires the aggregation wrapped under {"aggs": ...}
            # and sent as the "json-aggs" parameter (not "aggs").
            params["json-aggs"] = _json.dumps({"aggs": source.aggs})
            resp = dci_job.search(ctx, **params, limit=source.limit)
            if resp.status_code != 200:
                raise RuntimeError(f"DCI search failed ({resp.status_code}): {resp.text}")
            data = resp.json()
            if "aggregations" in data:
                if source.aggs_raw:
                    return [data["aggregations"]]
                return self._flatten_aggs(data["aggregations"])
            sources, _ = _hits_and_total(data)
        else:
            sources = _paginated_search(ctx, params, source.limit)

        if source.include_results or source.include_files:
            if source.include_files:
                self._download_files(ctx, sources, source.file_patterns)
            return sources

        return [_flatten_row(src, source.fields) for src in sources]

    def _download_files(self, ctx, jobs: list[dict], patterns: list[str] | None) -> None:
        for job in jobs:
            raw_files = job.get("files", [])
            enriched = []
            for f in raw_files:
                file_id = f.get("id")
                file_name = f.get("name", "")
                if not file_id:
                    continue
                if patterns and not any(re.search(p, file_name) for p in patterns):
                    continue
                print(f"    Downloading {file_name}...", file=sys.stderr)
                resp = dci_file.content(ctx, id=file_id)
                if resp.status_code == 200:
                    try:
                        content = resp.content.decode("utf-8")
                    except UnicodeDecodeError:
                        content = ""
                else:
                    print(
                        f"    Warning: failed to download {file_name} ({resp.status_code})",
                        file=sys.stderr,
                    )
                    content = ""
                enriched.append({"name": file_name, "id": file_id, "content": content})
            job["files"] = enriched

    def _transform_weekly_file_coverage(self, ctx, source: SourceConfig) -> list[dict]:
        """Paginated fetch of all jobs matching source.query, then aggregate:
        per remoteci, per ISO week → total jobs vs. jobs missing a file whose
        name matches any pattern in source.file_patterns (default: console.log)."""
        patterns = source.file_patterns or ["console\\.log$"]

        def _has_match(job: dict) -> bool:
            files = job.get("files") or []
            return any(
                re.search(p, f.get("name", ""), re.IGNORECASE)
                for f in files
                for p in patterns
            )

        def _week_start(date_str: str) -> str:
            dt = datetime.fromisoformat(date_str[:19])
            return (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")

        # Fetch all matching jobs (no upper bound)
        all_jobs = _paginated_search(
            ctx,
            {
                "query": source.query,
                "sort": source.sort,
                "fields": "id,remoteci.name,created_at,files.name",
            },
            max_results=sys.maxsize,
        )

        # Aggregate: remoteci → week → {total, with_log}
        rc_weeks: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"total": 0, "with_log": 0}))
        for job in all_jobs:
            rc = job.get("remoteci") or {}
            rc_name = rc.get("name", "unknown") if isinstance(rc, dict) else str(rc)
            date_str = job.get("created_at", "")
            if not date_str:
                continue
            week = _week_start(date_str)
            rc_weeks[rc_name][week]["total"] += 1
            if _has_match(job):
                rc_weeks[rc_name][week]["with_log"] += 1

        result = []
        for rc_name in sorted(rc_weeks):
            week_data = rc_weeks[rc_name]
            rc_total = sum(w["total"] for w in week_data.values())
            rc_with = sum(w["with_log"] for w in week_data.values())
            result.append({
                "remoteci": rc_name,
                "total": rc_total,
                "with_log": rc_with,
                "missing": rc_total - rc_with,
                "weeks": [
                    {
                        "week": week,
                        "total": wd["total"],
                        "with_log": wd["with_log"],
                        "missing": wd["total"] - wd["with_log"],
                    }
                    for week, wd in sorted(week_data.items())
                ],
            })
        return result

    def _flatten_aggs(self, aggs: dict) -> list[dict]:
        rows = []
        for agg_name, agg_data in aggs.items():
            if "buckets" in agg_data:
                for bucket in agg_data["buckets"]:
                    rows.append({"key": bucket.get("key"), "count": bucket.get("doc_count")})
            elif "value" in agg_data:
                rows.append({"key": agg_name, "value": agg_data["value"]})
        return rows
