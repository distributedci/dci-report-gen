from dci_report_gen.config import SourceConfig
from dci_report_gen.fetchers.markdown_table import (
    MarkdownTableFetcher,
    parse_markdown_tables,
)

SAMPLE = """# Title

Intro text with a | pipe.

## Numeric IDs

| ID | Batch | Suite |
| --- | :---: | --- |
| BM 1.0 | [1-1](docs/b1.md) | a.robot |
| PTP 2.0 | Not found | b\\|c.robot |
| ROB 5.3 | 3-1 |

## Other

|A|B|
|-|-|
"""

SAMPLE_2 = SAMPLE + """
|K|V|
|---|---|
|x|y|
"""


def test_parse_rows_and_headers():
    rows = parse_markdown_tables(SAMPLE)
    assert len(rows) == 3
    assert rows[0] == {
        "ID": "BM 1.0",
        "Batch": "[1-1](docs/b1.md)",
        "Suite": "a.robot",
        "_table": 0,
        "_heading": "Numeric IDs",
    }
    assert rows[1]["Suite"] == "b|c.robot"
    # Missing trailing cells are padded.
    assert rows[2]["Suite"] == ""


def test_table_index_and_heading():
    rows = parse_markdown_tables(SAMPLE_2)
    last = rows[-1]
    assert last["K"] == "x" and last["_table"] == 2
    assert last["_heading"] == "Other"


def test_fetcher_relative_path_and_table_filter(tmp_path):
    (tmp_path / "ids.md").write_text(SAMPLE_2)
    src = SourceConfig(
        type="markdown_table",
        params={"path": "ids.md", "base_dir": str(tmp_path), "table": 2},
    )
    rows = MarkdownTableFetcher().fetch(src)
    assert [r["K"] for r in rows] == ["x"]
