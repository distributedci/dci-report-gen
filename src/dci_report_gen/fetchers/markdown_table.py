"""Read rows from the pipe tables of a local Markdown file.

Lets a report join DCI data with reference data maintained in a repository
(for example a test ID -> batch mapping) without copying it into the config.

Config:

    data:
      ids:
        type: markdown_table
        params:
          path: ../ROBOT_TEST_IDS.md   # relative to the config file
          table: 0                     # optional: index of the table to read

Each row is a dict keyed by the table header cells, plus:
  - ``_table``: index of the table in the file (0-based)
  - ``_heading``: text of the closest Markdown heading above the table
Cells are returned verbatim (Markdown links are kept).
"""

from __future__ import annotations

import re
from pathlib import Path

from dci_report_gen.config import SourceConfig

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_SEPARATOR = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _split_row(line: str) -> list[str]:
    line = line.strip().removeprefix("|")
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells = re.split(r"(?<!\\)\|", line)
    return [c.strip().replace("\\|", "|") for c in cells]


def parse_markdown_tables(text: str) -> list[dict]:
    rows: list[dict] = []
    lines = text.splitlines()
    heading = ""
    table_index = -1
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _HEADING.match(line)
        if m:
            heading = m.group(1)
            i += 1
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if "|" in line and "|" in nxt and _SEPARATOR.match(nxt.strip()):
            table_index += 1
            headers = _split_row(line)
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = _split_row(lines[i])
                cells += [""] * (len(headers) - len(cells))
                row = dict(zip(headers, cells))
                row["_table"] = table_index
                row["_heading"] = heading
                rows.append(row)
                i += 1
            continue
        i += 1
    return rows


class MarkdownTableFetcher:
    def fetch(self, source: SourceConfig) -> list[dict]:
        params = source.params or {}
        path = params.get("path")
        if not path:
            raise ValueError("markdown_table source requires params.path")
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path(params.get("base_dir", ".")) / p
        rows = parse_markdown_tables(p.read_text())
        table = params.get("table")
        if table is not None:
            rows = [r for r in rows if r["_table"] == int(table)]
        return rows
