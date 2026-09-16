import argparse
import sys

from dotenv import load_dotenv

from dci_report_gen.config import MissingVariablesError, load_config
from dci_report_gen.engine import ReportEngine


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="dci-report-gen",
        description="Generate reports from DCI, Jira, and GitHub data",
    )
    parser.add_argument("config", nargs="?", help="Path to YAML config file")
    parser.add_argument(
        "-o",
        "--output",
        help="Output file path (.pdf or .md extension determines format)",
    )
    parser.add_argument(
        "--var",
        action="append",
        metavar="KEY=VALUE",
        help="Override config vars (e.g., --var date_start=2024-06-01)",
    )
    parser.add_argument(
        "--list-templates",
        action="store_true",
        help="List available predefined templates",
    )

    args = parser.parse_args()

    if args.list_templates:
        from dci_report_gen.templates.registry import list_templates

        templates = list_templates()
        if not templates:
            print("No templates registered.")
        else:
            for name, desc in templates:
                print(f"  {name}: {desc}")
        sys.exit(0)

    if not args.config:
        parser.error("config file is required")

    var_overrides = {}
    if args.var:
        for item in args.var:
            key, sep, value = item.partition("=")
            if not sep:
                parser.error(f"Invalid --var format: {item} (expected KEY=VALUE)")
            var_overrides[key] = value

    try:
        config = load_config(args.config, var_overrides)
    except MissingVariablesError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    output = args.output
    if not output:
        output = config.title.lower().replace(" ", "-") + ".pdf"

    engine = ReportEngine()
    engine.generate(config, output, config_path=args.config)


if __name__ == "__main__":
    main()
