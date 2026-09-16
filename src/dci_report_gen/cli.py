import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from dci_report_gen.config import load_config
from dci_report_gen.engine import ReportEngine

# Project root is 3 levels up from this file: src/dci_report_gen/cli.py
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main():
    # Load .env from the project root so creds are found regardless of CWD.
    load_dotenv(_PROJECT_ROOT / ".env", override=True)

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

    config = load_config(args.config, var_overrides)

    output = args.output
    if not output:
        output = config.title.lower().replace(" ", "-") + ".pdf"

    engine = ReportEngine()
    engine.generate(config, output, config_path=args.config)


if __name__ == "__main__":
    main()
