import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path

import jinja2
import jinja2.meta
import yaml

_BUILTIN_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class ColumnConfig:
    header: str
    field: str
    format: str | None = None


@dataclass
class RenderConfig:
    style: str = "table"
    columns: list[ColumnConfig] | None = None
    title: str | None = None


@dataclass
class SourceConfig:
    type: str
    query: str | None = None
    fields: list[str] | None = None
    limit: int = 100
    sort: str = "-created_at"
    aggs: dict | None = None
    aggs_raw: bool = False
    transform: str | None = None
    jql: str | None = None
    max_results: int = 50
    repo: str | None = None
    include_results: bool = False
    include_files: bool = False
    file_patterns: list[str] | None = None
    params: dict | None = None


@dataclass
class SectionConfig:
    name: str
    source: SourceConfig
    render: RenderConfig


@dataclass
class TemplateRef:
    type: str
    params: dict = field(default_factory=dict)


@dataclass
class ReportConfig:
    title: str
    sections: list[SectionConfig]
    author: str | None = None
    date: str = "auto"
    vars: dict[str, str] = field(default_factory=dict)
    template: TemplateRef | None = None
    layout: str | None = None
    data: dict[str, SourceConfig] | None = None
    context: dict = field(default_factory=dict)


class MissingVariablesError(ValueError):
    """Raised when the config references variables that are not defined."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        lines = "\n".join(f"  --var {name}=VALUE" for name in missing)
        super().__init__(
            f"The following variables are referenced but not defined:\n{lines}"
        )


def _collect_var_refs(obj) -> set[str]:
    """Recursively walk str/dict/list YAML values and return all {{var}} variable names."""
    pattern = re.compile(r"\{\{(\w+)(?:[*+\-]\d+)?\}\}")
    found: set[str] = set()
    if isinstance(obj, str):
        found.update(pattern.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            found.update(_collect_var_refs(v))
    elif isinstance(obj, list):
        for item in obj:
            found.update(_collect_var_refs(item))
    return found


def _find_template_file(layout: str, config_dir: Path) -> Path | None:
    """Find a template file by trying config_dir first, then the built-in templates dir."""
    candidate = config_dir / layout
    if candidate.exists():
        return candidate
    candidate = _BUILTIN_TEMPLATES_DIR / layout
    if candidate.exists():
        return candidate
    return None


def _collect_jinja2_undeclared(template_path: Path) -> set[str]:
    """Return the set of undeclared variables in a Jinja2 template file."""
    env = jinja2.Environment()
    source = template_path.read_text()
    ast = env.parse(source)
    return jinja2.meta.find_undeclared_variables(ast)


def _preflight_validate(
    raw: dict,
    resolved_vars: dict[str, str],
    layout: str | None,
    config_path: str,
    all_data_names: set[str],
) -> None:
    """Validate that all referenced variables are defined.

    Raises MissingVariablesError if any variable references cannot be resolved.
    """
    # (a) Scan the config body (excluding the 'vars' block) for {{var}} references
    body = {k: v for k, v in raw.items() if k != "vars"}
    refs_in_body = _collect_var_refs(body)
    missing_in_body = refs_in_body - set(resolved_vars)

    # (b) If layout is set, check the Jinja2 template for undeclared variables
    missing_in_template: set[str] = set()
    if layout:
        config_dir = Path(config_path).parent
        template_path = _find_template_file(layout, config_dir)
        if template_path is not None:
            undeclared = _collect_jinja2_undeclared(template_path)
            available = (
                {"title", "author", "date"}
                | set(resolved_vars)
                | set(raw.get("context", {}).keys())
                | all_data_names
            )
            missing_in_template = undeclared - available

    # (c) Union both missing sets and raise if non-empty
    missing = missing_in_body | missing_in_template
    if missing:
        raise MissingVariablesError(sorted(missing))


def _resolve_date_expr(text: str) -> str:
    """Resolve date expressions: {today}, {today-Nd}, {today+Nd}."""

    def replacer(match: re.Match) -> str:
        expr = match.group(1)
        if expr == "today":
            return datetime.datetime.now(tz=datetime.timezone.utc).date().isoformat()
        m = re.match(r"today([+-])(\d+)d", expr)
        if m:
            sign = 1 if m.group(1) == "+" else -1
            days = int(m.group(2))
            return (
                datetime.datetime.now(tz=datetime.timezone.utc).date()
                + datetime.timedelta(days=sign * days)
            ).isoformat()
        raise ValueError(f"Unknown date expression: {{{expr}}}")

    return re.sub(r"\{(today(?:[+-]\d+d)?)\}", replacer, text)


def _substitute_vars_expr(text: str, vars: dict[str, str]) -> str:
    """Substitute {{var}} and {{var*N}} / {{var+N}} / {{var-N}} expressions."""

    def replacer(match: re.Match) -> str:
        expr = match.group(1)
        # Simple variable reference: {{var}}
        m = re.match(r"^(\w+)$", expr)
        if m:
            key = m.group(1)
            if key not in vars:
                raise ValueError(f"Undefined variable: {{{{{key}}}}}")
            return vars[key]
        # Arithmetic: {{var*N}}, {{var+N}}, {{var-N}}
        m = re.match(r"^(\w+)([*+\-])(\d+)$", expr)
        if m:
            key, op, operand = m.group(1), m.group(2), int(m.group(3))
            if key not in vars:
                raise ValueError(f"Undefined variable: {{{{{key}}}}}")
            try:
                val = int(vars[key])
            except ValueError:
                raise ValueError(
                    f"Cannot apply arithmetic to non-integer var {{{{{key}}}}}={vars[key]}"
                )
            if op == "*":
                return str(val * operand)
            elif op == "+":
                return str(val + operand)
            else:
                return str(val - operand)
        raise ValueError(f"Invalid expression: {{{{{expr}}}}}")

    return re.sub(r"\{\{(\w+(?:[*+\-]\d+)?)\}\}", replacer, text)


def _resolve_vars(vars: dict[str, str]) -> dict[str, str]:
    """Resolve inter-var references, arithmetic, and date expressions.

    Processing order:
    1. Collect literal vars (no {{}} references)
    2. Resolve {{var}} and {{var*N}} references using literals
    3. Resolve {today}, {today-Nd}, {today+Nd} date expressions
    """
    str_vars = {k: str(v) for k, v in vars.items()}

    # Pass 1: collect literals (no {{}} references)
    literals: dict[str, str] = {}
    pending: dict[str, str] = {}
    for key, value in str_vars.items():
        if "{{" in value:
            pending[key] = value
        else:
            literals[key] = value

    # Pass 2: resolve {{var}} and {{var*N}} in pending vars using literals
    resolved = dict(literals)
    for key, value in pending.items():
        resolved[key] = _substitute_vars_expr(value, resolved)

    # Pass 3: resolve date expressions
    resolved = {k: _resolve_date_expr(v) for k, v in resolved.items()}

    return resolved


def _substitute_vars(text: str, vars: dict[str, str]) -> str:
    def replacer(match):
        key = match.group(1)
        if key not in vars:
            raise ValueError(f"Undefined variable: {{{{{key}}}}}")
        return vars[key]

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replacer, text)


def _apply_vars_to_source(source: SourceConfig, vars: dict[str, str]) -> None:
    if source.query:
        source.query = _substitute_vars(source.query, vars)
    if source.jql:
        source.jql = _substitute_vars(source.jql, vars)


def _parse_columns(raw: list[dict]) -> list[ColumnConfig]:
    return [
        ColumnConfig(
            header=c["header"],
            field=c["field"],
            format=c.get("format"),
        )
        for c in raw
    ]


def _parse_render(raw: dict) -> RenderConfig:
    columns = None
    if "columns" in raw:
        columns = _parse_columns(raw["columns"])
    return RenderConfig(
        style=raw.get("style", "table"),
        columns=columns,
        title=raw.get("title"),
    )


def _parse_source(raw: dict) -> SourceConfig:
    return SourceConfig(
        type=raw["type"],
        query=raw.get("query"),
        fields=raw.get("fields"),
        limit=int(raw.get("limit", 100)),
        sort=raw.get("sort", "-created_at"),
        aggs=raw.get("aggs"),
        aggs_raw=bool(raw.get("aggs_raw", False)),
        transform=raw.get("transform"),
        jql=raw.get("jql"),
        max_results=int(raw.get("max_results", 50)),
        repo=raw.get("repo"),
        include_results=bool(raw.get("include_results", False)),
        include_files=bool(raw.get("include_files", False)),
        file_patterns=raw.get("file_patterns"),
        params=raw.get("params"),
    )


def _parse_sections(raw: list[dict]) -> list[SectionConfig]:
    return [
        SectionConfig(
            name=s["name"],
            source=_parse_source(s["source"]),
            render=_parse_render(s["render"]),
        )
        for s in raw
    ]


def load_config(
    path: str, var_overrides: dict[str, str] | None = None
) -> ReportConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)

    report = raw.get("report", {})
    title = report.get("title", "Untitled Report")
    author = report.get("author")
    report_date = report.get("date", "auto")
    if report_date == "auto":
        report_date = datetime.datetime.now(tz=datetime.timezone.utc).date().isoformat()

    vars = raw.get("vars", {})
    if var_overrides:
        vars.update(var_overrides)
    vars = _resolve_vars(vars)

    context = raw.get("context", {})

    layout = report.get("layout")

    template = None
    if "template" in raw:
        t = raw["template"]
        template = TemplateRef(type=t["type"], params=t.get("params", {}))

    template_sections: list[SectionConfig] = []
    template_data: dict[str, SourceConfig] = {}

    if template:
        from dci_report_gen.templates.registry import expand_template

        tmpl_raw, tmpl_vars = expand_template(template.type, template.params)
        tmpl_vars.update(vars)
        vars = tmpl_vars

        tmpl_report = tmpl_raw.get("report", {})
        if title == "Untitled Report" and "title" in tmpl_report:
            title = tmpl_report["title"]
        if author is None and "author" in tmpl_report:
            author = tmpl_report["author"]
        if layout is None and "layout" in tmpl_report:
            layout = tmpl_report["layout"]

        if "sections" in tmpl_raw:
            template_sections = _parse_sections(tmpl_raw["sections"])

        if "data" in tmpl_raw:
            template_data = {
                name: _parse_source(src_raw)
                for name, src_raw in tmpl_raw["data"].items()
            }

    # Pre-flight validation: check all variable references can be resolved
    all_data_names = set(template_data) | set(raw.get("data", {}))
    _preflight_validate(raw, vars, layout, path, all_data_names)

    sections = []
    if "sections" in raw:
        sections = _parse_sections(raw["sections"])

    data_sources = dict(template_data)
    if "data" in raw:
        for name, src_raw in raw["data"].items():
            data_sources[name] = _parse_source(src_raw)

    all_sections = template_sections + sections

    for section in all_sections:
        _apply_vars_to_source(section.source, vars)
        if section.name:
            section.name = _substitute_vars(section.name, vars)

    for source in data_sources.values():
        _apply_vars_to_source(source, vars)

    resolved_title = _substitute_vars(title, vars) if "{{" in title else title

    return ReportConfig(
        title=resolved_title,
        author=author,
        date=report_date,
        sections=all_sections,
        vars=vars,
        template=template,
        layout=layout,
        data=data_sources if data_sources else None,
        context=context,
    )
