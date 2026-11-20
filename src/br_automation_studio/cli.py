
"""Standalone command-line access to the B&R operation catalog."""

import argparse
from html import escape
import json
from pathlib import Path
import sys

from .json_io import read_json
from .operations import OPERATIONS, SCHEMA_VERSION, execute

REQUEST_LIMIT = 1024 * 1024
EXIT_CODES = {"complete": 0, "issues": 1, "invalid_request": 2, "partial": 3}


def markdown(response):
    report = response["result"]

    def text(value):
        if value is None:
            return "—"
        return escape(str(value), quote=False).replace("\r", " ").replace("\n", " ").replace("|", "&#124;")

    if "rucReview" in report:
        return markdown_ruc(response, text)

    titles = {
        "project_inspect": "Saved Automation Studio project inspection",
        "build_preflight": "AS4 build preflight",
        "build_plan": "AS4 native build plan",
        "build_execute": "AS4 native build receipt",
    }
    lines = [
        f"# {titles[response['operation']]}", "",
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
    if preflight := report.get("preflight"):
        lines.extend(["", "## Installation evidence", "",
                      f"- Studio read-set SHA-256 **{preflight['studioSnapshot']['sha256']}**",
                      f"- Runtime read-set SHA-256 **{preflight['runtimeSnapshot']['sha256']}**"])
        for builder in preflight["buildTools"]:
            lines.append(f"- Builder {text(builder['path'])} · AS {text(builder['studioVersion'])}")
        runtime = preflight["runtime"]
        lines.append(f"- Runtime {text(runtime['version'])} · catalog {text(runtime['manifest'])}")
        for item in preflight["technologyPackages"]:
            lines.append(f"- {text(item['name'])} {text(item['version'])} · internal build {text(item['internalVersion'])}")
        for dependency in preflight["libraryDependencies"]:
            versions = ", ".join(dependency["selectedVersions"]) or "unresolved"
            lines.append(f"- {text(dependency['library'])} requires {text(dependency['requires'])} · {text(versions)}")
    if plan := report.get("buildPlan"):
        lines.extend(["", "## Build plan", "",
                      f"- SHA-256 **{plan['sha256']}**",
                      f"- Staged files **{len(plan['manifest']['inputs']['files'])}**"])
    if job := report.get("nativeBuild"):
        lines.extend(["", "## Native job", "",
                      f"- Job {text(job['jobId'])} · {text(job['status'])} at {text(job['directory'])}"])
        if summary := job.get("summary"):
            lines.append(f"- Builder reported {summary['errors']} errors and {summary['warnings']} warnings")
        if package := job["package"]:
            lines.append(f"- RUC {text(package['path'])} · {package['entries']} ZIP entries · {package['sha256']}")
    lines.extend(["", "## Diagnostics", ""])
    for item in report["diagnostics"]:
        scope = f"{text(item['scope'])} · " if "scope" in item else ""
        lines.append(f"- {text(item['severity'])} · {scope}{text(item['path'])} · {text(item['message'])}")
    if not report["diagnostics"]:
        lines.append("No issues reported by the selected checks.")
    return "\n".join(lines)


def markdown_ruc(response, text):
    report = response["result"]
    review = report["rucReview"]
    lines = [
        "# AS4 RUC review", "",
        f"- Project **{text(report['project'])}**",
        f"- Readback status **{response['status']}**",
        f"- Configuration **{text(report['selectedConfiguration'])}** · CPU **{text(report['selectedCpu'])}**",
        f"- Build job {text(review['jobId'])}",
        f"- Build plan SHA-256 {text(review['planSha256'])}",
        f"- Saved source matched **{'yes' if review['sourceMatched'] else 'unverified'}**",
    ]
    if archive := review["archive"]:
        lines.extend([
            f"- RUC SHA-256 **{archive['sha256']}** · {archive['entries']} archive entries",
            f"- 6D module elements read {len(review['modules'])} · Rendered widgets read {len(review['widgets'])}",
        ])
    if summary := review["buildSummary"]:
        lines.append(f"- Builder errors {text(summary['errors'])} · Builder warnings {text(summary['warnings'])}")
    for manager in review["trafficManagers"]:
        lines.extend(["", f"## {text(manager['manager'])}", ""])
        for edge in manager["edges"]:
            lines.append(f"- {text(edge['source'])} ({text(edge['sourceType'])}) → {text(edge['destination'])} ({text(edge['destinationType'])})")
    if review["widgets"]:
        lines.extend(["", "## Rendered widgets", ""])
        for item in review["widgets"]:
            lines.append(f"- {text(item['contentId'])} / {text(item['id'])} · assembly {text(item['assembly'])} · manager {text(item['manager'])}")
    lines.extend(["", "## Diagnostics", ""])
    for item in report["diagnostics"]:
        lines.append(f"- {text(item['severity'])} · {text(item['path'])} · {text(item['message'])}")
    if not report["diagnostics"]:
        lines.append("The selected package checks matched the saved inputs.")
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
        response = execute(read_json(args.request, REQUEST_LIMIT), args.request.parent)
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
