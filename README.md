# dci-report-gen

A YAML-driven report generation engine for [DCI](https://docs.distributed-ci.io/) (Distributed CI), Jira, and GitHub data. Define your report in a YAML config file with a Jinja2 template for layout, and the engine fetches data from the APIs and produces PDF or Markdown output.

## Installation

```bash
pip install -e ".[dev]"
```

Or with [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync --extra dev
```

### System dependencies (for PDF output)

WeasyPrint requires system libraries for PDF rendering.

On Fedora/RHEL:

```bash
dnf install pango cairo gdk-pixbuf2
```

On Debian/Ubuntu:

```bash
apt install libpango-1.0-0 libcairo2 libgdk-pixbuf-2.0-0
```

On macOS (Homebrew):

```bash
brew install pango cairo glib
```

Then add to your `.env` so WeasyPrint can find them:

```env
DYLD_LIBRARY_PATH=/opt/homebrew/lib
```

## Quick start

```bash
# Generate a Markdown report
dci-report-gen config.yaml -o report.md

# Generate a PDF report
dci-report-gen config.yaml -o report.pdf

# Override variables from the command line
dci-report-gen config.yaml -o report.md --var date_start=2025-06-01

# List predefined templates
dci-report-gen --list-templates
```

With uv:

```bash
uv run --extra dev dci-report-gen config.yaml -o report.md
```

## Authentication

Credentials are read from environment variables. Create a `.env` file in the project root (it is gitignored):

```env
# DCI (required for DCI data)
DCI_CS_URL=https://api.distributed-ci.io
DCI_CLIENT_ID=remoteci/<your-client-id>
DCI_API_SECRET=<your-api-secret>

# Jira (required for Jira data)
JIRA_URL=https://redhat.atlassian.net
JIRA_TOKEN=<your-jira-token>
JIRA_EMAIL=<your-email>          # required for Atlassian Cloud (uses basic_auth)

# GitHub (required for GitHub data)
GITHUB_TOKEN=<your-github-token>

# macOS only — WeasyPrint PDF support
DYLD_LIBRARY_PATH=/opt/homebrew/lib
```

Only the credentials for data sources used in your config are required.

## Config file format

A config file defines the report metadata, named data sources, variables, and a Jinja2 layout template.

```yaml
report:
  title: "Weekly CI Report"
  author: "DCI Team"
  date: auto
  layout: weekly-report.md.j2

vars:
  date_start: "2025-06-01"

data:
  daily_jobs:
    type: dci
    query: "(((tags in ['daily']) and (components.type='ocp')) and (created_at>='{{date_start}}'))"
    fields: ["id", "name", "status", "created_at", "duration"]
    limit: 20
    sort: "-created_at"
  open_tickets:
    type: jira
    jql: "project = CILAB AND status = Open"
    max_results: 50
```

The engine fetches all named data sources, then passes them as context variables to the Jinja2 template.

### Data sources

Each entry in the `data` block defines a named data source.

**DCI** (`type: dci`) — searches DCI jobs via the analytics API:

| Field             | Description                                       | Default       |
|-------------------|---------------------------------------------------|---------------|
| `query`           | DCI search query (parenthesized DSL)              | required      |
| `fields`          | List of fields to return                          | all           |
| `limit`           | Max number of results                             | `100`         |
| `sort`            | Sort field (prefix `-` for desc)                  | `-created_at` |
| `aggs`            | Elasticsearch aggregation object                  | none          |
| `include_results` | Include test result counts (`results`, `tests`)   | `false`       |
| `include_files`   | Download job file contents                        | `false`       |
| `file_patterns`   | List of regex patterns to filter files            | all           |

**Jira** (`type: jira`) — runs a JQL query:

| Field         | Description                  | Default                                    |
|---------------|------------------------------|--------------------------------------------|
| `jql`         | JQL query string             | required                                   |
| `max_results` | Max number of results        | `50`                                       |
| `fields`      | List of fields to extract    | `key`, `summary`, `status`, `assignee`     |

**GitHub** (`type: github`) — searches issues and pull requests:

| Field         | Description               | Default                                    |
|---------------|---------------------------|--------------------------------------------|
| `query`       | GitHub search query       | required                                   |
| `max_results` | Max number of results     | `50`                                       |
| `fields`      | List of fields to extract | `number`, `title`, `state`, `author`       |

**PR ↔ Jira Audit** (`type: pr_jira_audit`) — cross-references GitHub PRs with Jira tickets:

| Field         | Description                                           | Default  |
|---------------|-------------------------------------------------------|----------|
| `query`       | GitHub search query for PRs                           | required |
| `max_results` | Max number of PRs to fetch                            | `200`    |
| `params.jira_projects` | Comma-separated Jira project keys to search  | required |

For each PR, the fetcher searches Jira for tickets referencing the PR URL (in description, comments, or web links) using exact-phrase JQL matching. Returns a list of dicts with `number`, `title`, `url`, `repo`, `author`, `created_at`, `linked` (bool), and `jira_keys` (list).

**Jira Backlog Trend** (`type: jira_backlog_trend`) — reconstructs the open-backlog count at a series of past dates for trend charting:

| Field                 | Description                                               | Default  |
|-----------------------|-----------------------------------------------------------|----------|
| `params.base_jql`     | Base JQL scope, e.g. `project = CILAB`                    | required |
| `params.start_date`   | First sample date, `YYYY-MM-DD`                           | required |
| `params.end_date`     | Last sample date, `YYYY-MM-DD`                            | today    |
| `params.granularity`  | Sampling step: `daily`, `weekly`, or `monthly`           | `weekly` |

For each sampled date `D`, a ticket counts as **open** when it was created on or before `D` and not yet resolved by `D` (`created <= D AND (resolutiondate is EMPTY OR resolutiondate > D)`). It additionally counts as **active** when it is assigned (`assignee is not EMPTY`) — a count-based approximation using the *current* assignee state. The **ratio** is `active / open`, i.e. the share of the open backlog being worked on. Alongside these point-in-time **stock** counts, each row also carries the per-period **flow** counts over the interval since the previous sample: **created** (`created > prev AND created <= D`) and **closed** (`resolutiondate > prev AND resolutiondate <= D`); both are `0` for the first sample. The stock-flow identity holds: `open[i] - open[i-1] == created[i] - closed[i]`. Counts use Jira's lightweight `approximate-count` endpoint (no issue payloads), so the fetcher is cheap even over long ranges. Returns a list of dicts `{"date": "YYYY-MM-DD", "open": <int>, "active": <int>, "ratio": <float>, "created": <int>, "closed": <int>}` sorted ascending by date. Pair it with the `trend_chart_svg` Jinja filter to render inline SVG line charts (see `examples/jira-backlog-trend.md.j2`), overlaying multiple series — e.g. open vs active for the stock, and created vs closed for the flow.

Scope is purely driven by `base_jql`, so the same config serves any project, team label, or issue type. For a **bug-only** backlog & flow, override `base_jql` with `AND issuetype = Bug` (and set `scope=Bugs` for the header) — no separate config needed.

### Variables

The `vars` block defines variables substituted into data source queries using `{{var_name}}` syntax. Variables can be overridden from the CLI with `--var KEY=VALUE`.

## Jinja2 layout templates

The `layout` field in the config points to a Jinja2 template file (`.md.j2`). The template receives all fetched data sources as variables, plus `title`, `author`, `date`, and any `vars`.

Templates are searched in this order:
1. The directory containing the YAML config file
2. The built-in templates directory (`src/dci_report_gen/templates/`)

### Example template (`weekly-report.md.j2`)

```jinja
# {{ title }}

**Author:** {{ author }}
**Date:** {{ date }}

## Recent OCP Daily Jobs

| Name | Status | Date | Duration | Link |
| ---- | ------ | ---- | -------- | ---- |
{% for job in daily_jobs %}
| {{ job.name }} | {{ job.status }} | {{ job.created_at | date }} | {{ job.duration | duration }} | [View](https://www.distributed-ci.io/jobs/{{ job.id }}) |
{% endfor %}
```

### Custom filters

| Filter               | Description                                               | Example                                    |
|----------------------|-----------------------------------------------------------|--------------------------------------------|
| `duration`           | Seconds → `Xh Ym Zs`                                     | `{{ job.duration \| duration }}`           |
| `compact_duration`   | Seconds → `1h09m` or `48m40s` (compact, no seconds for ≥1h) | `{{ job.duration \| compact_duration }}` |
| `human_duration`     | Seconds → `Xm YYs` (or long form)                        | `{{ job.duration \| human_duration }}`     |
| `date`               | ISO timestamp → `YYYY-MM-DD HH:MM`                        | `{{ job.created_at \| date }}`             |
| `jira_link`          | Jira key → Markdown link                                  | `{{ key \| jira_link }}`                   |
| `github_link`        | PR number → Markdown link                                 | `{{ pr \| github_link(repo) }}`            |
| `dci_link`           | DCI job ID → short linked ID                              | `{{ job.id \| dci_link }}`                 |
| `short_id`           | Truncate a UUID to N chars (default 8)                    | `{{ job.id \| short_id }}`                 |
| `status_emoji`       | DCI/test status → emoji (✅ ❌ 🔄)                         | `{{ job.status \| status_emoji }}`         |
| `find_testcase`      | Find a testcase by name in a tests list                   | `{{ job.tests \| find_testcase('hwlat') }}`|
| `find_file`          | Find file content by regex pattern                        | `{{ job.files \| find_file('timing') }}`   |
| `regex_extract`      | Extract first capture group from text                     | `{{ text \| regex_extract('Version: (.+)')}}` |
| `yaml_path`          | Parse YAML text and extract a dotted key path             | `{{ text \| yaml_path('spec.version') }}`  |
| `github_run_link`    | Find GitHub Actions run link from job tags                | `{{ job.tags \| github_run_link(repo) }}`  |
| `trend_chart_svg`    | Rows `[{date, open}]` → inline SVG line chart (one or more series via `series=[...]`) | `{{ backlog \| trend_chart_svg(title="Backlog", series=[{"key":"open","label":"Open"},{"key":"active","label":"Active"}]) }}` |

## Examples

### Weekly OCP daily jobs report

Fetches DCI jobs tagged `daily` for OCP components:

```bash
dci-report-gen examples/weekly-report.yaml -o report.md
dci-report-gen examples/weekly-report.yaml -o report.pdf
```

### PR ↔ Jira traceability audit

Finds all open PRs for a given GitHub user and checks which ones have a corresponding Jira ticket:

```bash
dci-report-gen examples/pr-jira-audit.yaml -o audit.md --var github_username=fredericlepied
```

### Jira open-backlog trend

Reconstructs the open-ticket count (stock) plus per-period created/closed (flow) at each sampled date and renders two trend charts. Defaults to CILAB, all issue types, weekly, from `2026-04-06` to today:

```bash
dci-report-gen examples/jira-backlog-trend.yaml -o backlog.md
dci-report-gen examples/jira-backlog-trend.yaml -o backlog.pdf

# Other project / range / granularity:
dci-report-gen examples/jira-backlog-trend.yaml -o backlog.pdf \
  --var project=CNF --var start_date=2026-01-01 --var granularity=monthly

# Bug-only scope (same config, just narrow base_jql + set scope label):
dci-report-gen examples/jira-backlog-trend.yaml -o cilab-bugs.pdf \
  --var 'base_jql=project = CILAB AND issuetype = Bug' \
  --var scope=Bugs --var 'title=CILAB Bug Backlog & Flow Trend'

# Team-filtered bugs (e.g. E/// SLCM):
dci-report-gen examples/jira-backlog-trend.yaml -o erc-bugs.pdf \
  --var 'base_jql=project = CNF AND issuetype = Bug AND labels = Ericsson-Cloud-RAN-SLCM' \
  --var scope=Bugs --var 'title=E/// SLCM Bug Backlog & Flow Trend'
```

### Using a predefined template

```yaml
template:
  type: daily-status
  params:
    date_start: "2025-06-01"
    limit: 10
```

```bash
dci-report-gen examples/use-template.yaml -o report.md
```

## Predefined templates

| Name                    | Description                                      |
|-------------------------|--------------------------------------------------|
| `daily-status`          | Daily CI job status report                       |
| `weekly-testing-summary`| Weekly testing summary for SLCM/RAN CI testing  |

List available templates:

```bash
dci-report-gen --list-templates
```

### Writing a custom template

Place a YAML file in `src/dci_report_gen/templates/` alongside its `.md.j2` layout:

```yaml
description: "My custom report"

params:
  date_start:
    required: true
  limit:
    default: 50

report:
  title: "My Report — {{ date_start }}"
  layout: my-report.md.j2

data:
  jobs:
    type: dci
    query: "..."
    limit: "{{limit}}"
```

## Output formats

| Extension | Output                                              |
|-----------|-----------------------------------------------------|
| `.md`     | Markdown rendered by Jinja2                         |
| `.pdf`    | Markdown → HTML → PDF via WeasyPrint + CSS styling  |

## Legacy sections mode

For simple reports without a Jinja2 template, use the `sections` format:

```yaml
report:
  title: "Simple Report"

sections:
  - name: "OCP Jobs"
    source:
      type: dci
      query: "..."
      fields: ["id", "status"]
    render:
      style: table
      columns:
        - header: "Job ID"
          field: "id"
        - header: "Status"
          field: "status"
```

Supported render styles: `table`, `list`, `summary`, `count`.

## Project structure

```
src/dci_report_gen/
├── cli.py              # CLI entry point
├── config.py           # YAML config loading and dataclasses
├── engine.py           # Fetch → render pipeline
├── fetchers/
│   ├── dci.py          # DCI job search (dciclient)
│   ├── jira.py         # Jira JQL queries
│   ├── github.py       # GitHub issue/PR search
│   ├── pr_jira_audit.py # PR ↔ Jira cross-reference audit
│   └── jira_backlog_trend.py # Point-in-time open-backlog trend
├── renderers/
│   ├── jinja.py        # Jinja2 rendering + custom filters
│   ├── markdown.py     # Markdown renderer (legacy sections mode)
│   ├── pdf.py          # PDF renderer (legacy sections mode)
│   ├── formatters.py   # Shared value formatting (date, duration)
│   └── default.css     # PDF stylesheet
└── templates/
    ├── registry.py               # Template discovery and expansion
    ├── daily-status.yaml
    ├── daily-status.md.j2
    ├── weekly-testing-summary.yaml
    └── weekly-testing-summary.md.j2

examples/
├── weekly-report.yaml            # OCP daily jobs example
├── weekly-report.md.j2
├── pr-jira-audit.yaml            # PR ↔ Jira traceability audit
├── pr-jira-audit.md.j2
└── use-template.yaml             # Predefined template usage
```

## Development

```bash
uv sync --extra dev
uv run --extra dev pytest
uv run --extra dev ruff check src/ tests/
```

## License

Apache License 2.0
