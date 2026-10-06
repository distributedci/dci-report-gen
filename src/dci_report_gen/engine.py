from __future__ import annotations

import sys
from pathlib import Path

from dci_report_gen.config import ReportConfig

TEMPLATES_DIR = Path(__file__).parent / "templates"


class ReportEngine:
    def __init__(self):
        self._fetchers = {}

    def _get_fetcher(self, source_type: str):
        if source_type not in self._fetchers:
            if source_type == "dci":
                from dci_report_gen.fetchers.dci import DCIFetcher

                self._fetchers[source_type] = DCIFetcher()
            elif source_type == "jira":
                from dci_report_gen.fetchers.jira import JiraFetcher

                self._fetchers[source_type] = JiraFetcher()
            elif source_type == "github":
                from dci_report_gen.fetchers.github import GitHubFetcher

                self._fetchers[source_type] = GitHubFetcher()
            elif source_type == "pr_jira_audit":
                from dci_report_gen.fetchers.pr_jira_audit import PrJiraAuditFetcher

                self._fetchers[source_type] = PrJiraAuditFetcher()
            elif source_type == "jira_backlog_trend":
                from dci_report_gen.fetchers.jira_backlog_trend import (
                    JiraBacklogTrendFetcher,
                )

                self._fetchers[source_type] = JiraBacklogTrendFetcher()
            else:
                raise ValueError(f"Unknown source type: {source_type}")
        return self._fetchers[source_type]

    def generate(self, config: ReportConfig, output_path: str, config_path: str | None = None) -> None:
        if config.layout and not config.data:
            raise ValueError("Config has 'layout' but no 'data' block; Jinja2 mode requires both.")
        if config.layout and config.data:
            self._generate_jinja(config, output_path, config_path)
        else:
            self._generate_sections(config, output_path)

    def _generate_jinja(self, config: ReportConfig, output_path: str, config_path: str | None) -> None:
        from dci_report_gen.renderers.jinja import render_markdown, markdown_to_pdf

        fetched = {}
        for name, source in config.data.items():
            print(f"  Fetching: {name}...", file=sys.stderr)
            fetcher = self._get_fetcher(source.type)
            fetched[name] = fetcher.fetch(source)
            print(f"  Got {len(fetched[name])} rows.", file=sys.stderr)

        search_paths = [TEMPLATES_DIR]
        if config_path:
            search_paths.insert(0, str(Path(config_path).parent))

        context = {
            "title": config.title,
            "author": config.author or "",
            "date": config.date,
            **config.vars,
            **config.context,
            **fetched,
        }

        md_text = render_markdown(config.layout, search_paths, context)

        if output_path.endswith(".pdf"):
            markdown_to_pdf(md_text, output_path)
        else:
            with open(output_path, "w") as f:
                f.write(md_text)

        print(f"Report written to {output_path}", file=sys.stderr)

    def _generate_sections(self, config: ReportConfig, output_path: str) -> None:
        renderer = self._get_renderer(output_path)
        renderer.begin(config.title, config.author, config.date)

        for section in config.sections:
            print(f"  Fetching: {section.name}...", file=sys.stderr)
            fetcher = self._get_fetcher(section.source.type)
            data = fetcher.fetch(section.source)
            print(f"  Got {len(data)} rows.", file=sys.stderr)
            renderer.add_section(section.name, data, section.render)

        renderer.finish(output_path)
        print(f"Report written to {output_path}", file=sys.stderr)

    def _get_renderer(self, output_path: str):
        if output_path.endswith(".pdf"):
            from dci_report_gen.renderers.pdf import PDFRenderer

            return PDFRenderer()
        elif output_path.endswith(".md"):
            from dci_report_gen.renderers.markdown import MarkdownRenderer

            return MarkdownRenderer()
        else:
            raise ValueError(
                f"Unknown output format for {output_path}. Use .pdf or .md extension."
            )
