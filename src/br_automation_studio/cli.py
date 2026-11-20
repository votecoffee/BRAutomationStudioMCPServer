
"""Standalone command-line access to the B&R operation catalog."""

import argparse
from html import escape
import json
from pathlib import Path
import sys

from .operations import InvalidRequest, OPERATIONS, SCHEMA_VERSION, execute

REQUEST_LIMIT = 1024 * 1024
EXIT_CODES = {"complete": 0, "issues": 1, "invalid_request": 2, "partial": 3}


def unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise InvalidRequest(f"Duplicate JSON key {key!r}")
        data[key] = value
    return data


def invalid_constant(value):
    raise InvalidRequest(f"Invalid JSON constant {value!r}")


def load_request(path):
    with path.open("rb") as stream:
        data = stream.read(REQUEST_LIMIT + 1)
    if len(data) > REQUEST_LIMIT:
        raise InvalidRequest(f"Request exceeds {REQUEST_LIMIT} bytes")
    if data.startswith(b"\xef\xbb\xbf"):
        raise InvalidRequest("Write the request as UTF-8 without a BOM")
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant)


def markdown(response):
    report = response["result"]

    def text(value):
        if value is None:
            return "—"
        return escape(str(value), quote=False).replace("\r", " ").replace("\n", " ").replace("|", "&#124;")

    lines = [
        "# Saved Automation Studio project inspection", "",
        f"- Project **{text(report['project'])}**",
        f"- Status **{response['status']}**",
        f"- Configuration **{text(report['selectedConfiguration'])}**",
        f"- CPU **{text(report['selectedCpu'])}**",
        f"- Read-set SHA-256 **{report['snapshot']['sha256']}**",
        "",
        "## Declared technology packages", "",
    ]
    lines.extend(f"- {text(item['name'])} {text(item['version'])}" for item in report["technologyPackages"])
    lines.extend(["", "## Traffic Manager edges", ""])
    for manager in report["trafficManagers"]:
        for edge in manager["edges"]:
            lines.append(f"- {text(manager['manager'])} · {text(edge['source'])} → {text(edge['destination'])}")
    lines.extend(["", "## Assembly6D widgets", ""])
    for item in report["widgets"]:
        lines.append(f"- {text(item['id'])} · assembly {text(item['assembly'])} · manager {text(item['manager'])}")
    lines.extend(["", "## Diagnostics", ""])
    for item in report["diagnostics"]:
        lines.append(f"- {text(item['severity'])} · {text(item['path'])} · {text(item['message'])}")
    if not report["diagnostics"]:
        lines.append("No issues reported by the selected saved-file checks.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="br-studio")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("operations", help="List available operations")
    help_command = commands.add_parser("operation-help", help="Show an operation's request schema")
    help_command.add_argument("operation", choices=sorted(OPERATIONS))
    run = commands.add_parser("run", help="Run a UTF-8 JSON request")
    run.add_argument("--request", required=True, type=Path)
    run.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)

    if args.command == "operations":
        print(json.dumps({"schemaVersion": SCHEMA_VERSION, "operations": [
            {"name": item.name, "summary": item.summary} for item in OPERATIONS.values()
        ]}, indent=2))
        return 0
    if args.command == "operation-help":
        print(json.dumps(OPERATIONS[args.operation].request_schema(), indent=2))
        return 0

    try:
        response = execute(load_request(args.request), args.request.parent)
    except (ValueError, OSError, RecursionError) as error:
        print(json.dumps({"schemaVersion": SCHEMA_VERSION, "status": "invalid_request", "message": str(error)}, indent=2))
        return EXIT_CODES["invalid_request"]

    if args.format == "markdown":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(markdown(response))
    else:
        print(json.dumps(response, indent=2))
    return EXIT_CODES[response["status"]]
