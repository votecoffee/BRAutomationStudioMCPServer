
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
        "launchpad_route_review": "Saved Traffic Manager route review",
        "launchpad_station_review": "Saved Traffic Manager station review",
        "launchpad_recovery_review": "Saved Traffic Manager recovery review",
        "launchpad_geometry_review": "Saved Traffic Manager geometry review",
        "launchpad_layout_review": "Saved ACOPOS 6D segment layout review",
        "launchpad_frame_review": "Saved LaunchPad coordinate frame review",
        "build_preflight": "AS4 build preflight",
        "simulation_preflight": "AS4 ACOPOS 6D simulation preflight",
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
    if route := report.get("routeReview"):
        lines.extend(markdown_route(route, text))
        if stations := report.get("stationReview"):
            lines.extend(markdown_stations(stations, text))
        if recovery := report.get("recoveryReview"):
            lines.extend(markdown_recovery(recovery, text))
        if geometry := report.get("geometryReview"):
            lines.extend(markdown_geometry(geometry, text))
        if layout := report.get("layoutReview"):
            lines.extend(markdown_layout(layout, text))
        if frame := report.get("frameReview"):
            lines.extend(markdown_frame(frame, text))
    else:
        lines.extend(["", "## Traffic Manager edges", ""])
        for manager in report["trafficManagers"]:
            for edge in manager["edges"]:
                lines.append(f"- {text(manager['manager'])} · {text(edge['source'])} → {text(edge['destination'])}")
    lines.extend(markdown_rules(report["trafficManagers"], text))
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
    if simulation := report.get("simulationPreflight"):
        reference = simulation["reference"]
        installer = simulation["simulatorInstallation"]
        lines.extend(["", "## Complete simulation prerequisites", "",
                      f"- State **{text(simulation['state'])}** · Target mapp 6D {text(simulation['targetMapp6D'])}",
                      f"- Expected Planar Motor Simulation Tool {text(reference['simulatorVersion'])}",
                      f"- Windows Installer state {text(installer['state'])} ({text(installer['stateCode'])})"
                      f" · version {text(installer['version'])} · expected {text(reference['msiVersion'])}",
                      f"- Reference segment firmware {text(reference['segmentFirmware'])}"
                      f" · Application data {text(reference['segmentApplicationData'])}"])
        if not simulation["installedTargetCatalog"]:
            lines.append("- No installed catalog selectors for this family")
        for candidate in simulation["installedTargetCatalog"]:
            lines.append(f"- Catalog selector {text(candidate['selector'])} · build {text(candidate['internalVersion'])}"
                         f" · manifest verified {text(candidate['verified'])}")
        if media := simulation["simulatorZip"]:
            lines.append(f"- Simulator ZIP {text(media['path'])} · SHA-256 {text(media['sha256'])}"
                         f" · matches reviewed release {text(media['matched'])}")
        if export := simulation["launchPadExport"]:
            lines.append(f"- LaunchPad export {text(export['path'])} · SHA-256 {text(export['sha256'])}")
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
    smoke = report.get("arsimSmoke")
    binding = report.get("bindingReview")
    title = "# AS4 RUC review"
    if response["operation"] == "arsim_hmi_smoke":
        title = "# AS4 mapp View end-to-end smoke"
    elif response["operation"] == "launchpad_layout_review":
        title = "# Compiled ACOPOS 6D segment layout review"
    elif response["operation"] == "launchpad_frame_review":
        title = "# Compiled LaunchPad coordinate frame review"
    elif response["operation"] == "launchpad_geometry_review":
        title = "# Compiled Traffic Manager geometry review"
    elif response["operation"] == "launchpad_route_review":
        title = "# Compiled Traffic Manager route review"
    elif response["operation"] == "launchpad_station_review":
        title = "# Compiled Traffic Manager station review"
    elif response["operation"] == "launchpad_recovery_review":
        title = "# Compiled Traffic Manager recovery review"
    elif smoke is not None:
        title = "# AS4 ARsim loopback smoke"
    elif binding is not None:
        title = "# AS4 RUC mapp View binding review"
    lines = [
        title, "",
        f"- Project **{text(report['project'])}**",
        f"- Status **{response['status']}**",
        f"- Configuration **{text(report['selectedConfiguration'])}** · CPU **{text(report['selectedCpu'])}**",
        f"- Build job {text(review['jobId'])}",
        f"- Build plan SHA-256 {text(review['planSha256'])}",
        f"- Saved source matched **{'yes' if review['sourceMatched'] else 'unverified'}**",
    ]
    if archive := review["archive"]:
        lines.append(f"- RUC SHA-256 **{archive['sha256']}** · {archive['entries']} archive entries")
        if binding is None or review["modules"] or review["widgets"]:
            lines.append(f"- 6D module elements read {len(review['modules'])} · Rendered widgets read {len(review['widgets'])}")
    if summary := review["buildSummary"]:
        lines.append(f"- Builder errors {text(summary['errors'])} · Builder warnings {text(summary['warnings'])}")
    if smoke is not None:
        lines.extend(["", "## Loopback HTTP", "",
                      f"- Session {text(smoke['state'])} · Runtime {text(smoke.get('runtimeVersion'))}",
                      f"- Cleanup {text(smoke['cleanup'])}"])
        for asset in smoke["assets"]:
            lines.append(f"- {text(asset['path'])} · HTTP {asset['status']} · {asset['bytes']} bytes · {asset['sha256']}")
        if browser := smoke.get("browser"):
            lines.extend(markdown_browser(browser, text))
        if opcua := smoke.get("opcUa"):
            lines.extend(markdown_opcua("Direct OPC UA read", opcua, text))
        if hmi := smoke.get("hmi"):
            lines.extend(["", "## Same-session HMI comparison", "", f"- Phase {text(hmi['state'])}"])
            if comparison := hmi.get("comparison"):
                lines.append(f"- OPC UA before {text(comparison['directBeforeLast'])} · "
                             f"Browser {text(comparison['browserFirst'])} to {text(comparison['browserLast'])} · "
                             f"OPC UA after {text(comparison['directAfterFirst'])}")
            if first := hmi.get("opcUaBefore"):
                lines.extend(markdown_opcua("Direct OPC UA read before the browser", first, text))
            if browser := hmi.get("browser"):
                lines.extend(markdown_browser(browser, text))
            if last := hmi.get("opcUaAfter"):
                lines.extend(markdown_opcua("Direct OPC UA read after the browser", last, text))
    if binding is not None:
        lines.extend(["", "## Selected binding", "",
                      f"- {text(binding['state'])} · Visualization {text(binding['visualizationId'])}",
                      f"- {text(binding['stringIdentifier'])} → {text(binding['domId'])}"])
        for phase in ("saved", "compiled"):
            values = binding[phase]
            if values:
                lines.extend(["", f"### {phase.capitalize()}", ""])
                for key in ("visualization", "binding", "content", "connection", "nodeMap", "serverConfig"):
                    if key in values:
                        lines.append(f"- {text(key)} · {text(values[key])}")
                if endpoint := values.get("endpoint"):
                    lines.append(f"- {text(endpoint['host'])}:{text(endpoint['port'])} · {text(endpoint['namespaceUri'])}")
                if "accessLevel" in values:
                    lines.append(f"- Data type {text(values['dataType'])} · AccessLevel {text(values['accessLevel'])}")
    if route := report.get("routeReview"):
        lines.extend(markdown_route(route, text))
        if stations := report.get("stationReview"):
            lines.extend(markdown_stations(stations, text))
        if recovery := report.get("recoveryReview"):
            lines.extend(markdown_recovery(recovery, text))
        if geometry := report.get("geometryReview"):
            lines.extend(markdown_geometry(geometry, text))
        if layout := report.get("layoutReview"):
            lines.extend(markdown_layout(layout, text))
        if frame := report.get("frameReview"):
            lines.extend(markdown_frame(frame, text))
    else:
        for manager in review["trafficManagers"]:
            lines.extend(["", f"## {text(manager['manager'])}", ""])
            for edge in manager["edges"]:
                lines.append(f"- {text(edge['source'])} ({text(edge['sourceType'])}) → {text(edge['destination'])} ({text(edge['destinationType'])})")
    lines.extend(markdown_rules(review["trafficManagers"], text))
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


def markdown_rules(managers, text):
    if not managers:
        return []
    lines = ["", "## Traffic Manager rules", ""]
    for manager in managers:
        name = text(manager["manager"])
        if not manager["ruleEvidence"]:
            lines.append(f"- {name} has no resolved rule lists")
        for row in manager["ruleEvidence"]:
            lines.append(f"- {name} / {text(row['id'])} · {text(row['state'])} · "
                         f"{len(row['rules'])} supported rules · {text(row['path'])}")
            for rule in row["rules"]:
                roles = []
                for label in ("members", "entries", "exits"):
                    refs = ", ".join(f"{ref['name']} ({ref['type']})" for ref in rule[label])
                    roles.append(f"{label} {text(refs or None)}")
                lines.append(f"  - {text(rule['name'])} · " + " · ".join(roles))
    return lines


def markdown_route(route, text):
    lines = ["", "## LaunchPad route comparison", "",
             f"- {text(route['state'])} · {text(route['view'])} manager {text(route['managerId'])}"]
    if proof := route.get("launchPad"):
        lines.append(f"- LaunchPad {text(proof['launchPadVersion'])} · source SHA-256 {text(proof['sourceSha256'])}")
        lines.append(f"- Decoded JSON SHA-256 {text(proof['readbackSha256'])}")
        if proof["unusedGeometry"]:
            lines.append(f"- Geometry outside the cycle · {text(', '.join(proof['unusedGeometry']))}")
    if names := route.get("nameMap"):
        lines.append(f"- Name mapping SHA-256 {text(names['sha256'])}")
    if "assembly" in route:
        lines.append(f"- Assembly {text(route['assembly'])}")
    for item in route.get("expectedObjects", []):
        if item["name"] != item["importedName"]:
            lines.append(f"- Rename {text(item['name'])} → {text(item['importedName'])}")
    for label, key in (("Expected", "expectedEdges"), ("Missing", "missingEdges"), ("Unexpected", "unexpectedEdges")):
        for edge in route.get(key, []):
            lines.append(f"- {label} · {text(edge['source'])} ({text(edge['sourceType'])}) → "
                         f"{text(edge['destination'])} ({text(edge['destinationType'])}) · count {text(edge['count'])}")
    for label, key in (("Missing object", "missingObjects"), ("Additional object", "additionalObjects")):
        for obj in route.get(key, []):
            lines.append(f"- {label} · {text(obj['name'])} ({text(obj['type'])})")
    return lines


def markdown_stations(station_review, text):
    lines = ["", "## Station-to-station paths", "", f"- State {text(station_review['state'])}",
             "- Native Automation Studio recipe process and transition were not checked."]
    if not station_review["legs"]:
        return lines
    lines.extend(["", "| Traffic Manager path | Crosses return marker | Saved model steps (raw) | Fixed dwell |",
                  "| --- | --- | --- | --- |"])
    for leg in station_review["legs"]:
        path = " → ".join(obj["importedName"] for obj in leg["path"])
        wraps = "yes" if leg["wrapsReturnMarker"] else "no"
        model = leg["modelProcess"]
        steps = ", ".join(f"{step['type']} ({text(step['dwellTimeRaw'])})" for step in model["steps"])
        model_steps = text(steps or None)
        if model["state"] != "read":
            model_steps += " · unverified"
        lines.append(f"| {text(path)} | {wraps} | {model_steps} | {text(model['fixedDwellTime'])} |")
    return lines


def markdown_recovery(recovery, text):
    lines = ["", "## Station recovery declarations", "", f"- State {text(recovery['state'])}"]
    if not recovery["stations"]:
        return lines
    flags = ", ".join(f"{key}={text(value)}" for key, value in recovery["launchPadStartingPositions"].items())
    lines.append(f"- LaunchPad starting-position flags {flags}")
    lines.extend(["", "| Station | LaunchPad mode / station flag / explicit points | "
                  "AS4 mode / station flag / explicit points | Declarations agree | Saved to compiled |",
                  "| --- | --- | --- | --- | --- |"])
    for row in recovery["stations"]:
        before = recovery_fields(row["launchPad"], text)
        after = recovery_fields(row["automationStudio"], text)
        lines.append(f"| {text(row['importedName'])} | {before} | {after} | "
                     f"{text(row['declarationsMatch'])} | {text(row['savedToCompiled'])} |")
    return lines


def recovery_fields(row, text):
    return " / ".join(text(row[key]) for key in ("mode", "stationIsRecoveryPoint", "explicitPointCount"))


def markdown_geometry(geometry, text):
    lines = ["", "## Geometry comparison", "", f"- State {text(geometry['state'])}"]
    if "positions" not in geometry:
        return lines
    maximum = geometry["maxAbsDeltaMm"]
    a, b = geometry["maxPair"]
    orientation = geometry["handedness"]
    witness = " / ".join(orientation["witness"])
    lines.append(f"- Maximum pairwise absolute delta **{maximum:.3f} mm** · {text(a)} / {text(b)}")
    lines.append(f"- Handedness **{text(orientation['state'])}** · Witness {text(witness or None)}")
    if geometry["distanceToleranceMicrometres"] is not None:
        lines.append(f"- Distance tolerance {geometry['distanceToleranceMicrometres']} micrometres")
    if geometry["expectedHandedness"] is not None:
        lines.append(f"- Expected handedness {text(geometry['expectedHandedness'])}")
    lines.extend(["", "| Object pair | LaunchPad (mm) | Automation Studio (mm) | Delta (mm) |",
                  "| --- | ---: | ---: | ---: |"])
    for row in geometry["pairs"]:
        lines.append(f"| {text(row['from'])} / {text(row['to'])} | {row['launchPadMm']:.3f} | "
                     f"{row['automationStudioMm']:.3f} | {row['deltaMm']:+.3f} |")
    return lines


def markdown_layout(layout, text):
    lines = ["", "## Segment layout comparison", "", f"- State {text(layout['state'])}"]
    if "layoutId" not in layout:
        return lines
    lines.append(f"- Layout {text(layout['layoutId'])} · {text(layout['path'])}")
    lines.append(f"- Grid {layout['gridMm']} mm · segment {text(layout['segmentType'])}")
    mappings = layout["compatibleAxisMappings"]
    lines.append(f"- Compatible axis mappings from LaunchPad to Automation Studio "
                 f"{text(' / '.join(mappings) if mappings else 'none')}")
    if layout["userOriginRaw"] is not None:
        lines.append(f"- Automation Studio UserOrigin values {text(layout['userOriginRaw'])}")
    lines.extend(["", "| Input | Segment count | Master | Master-relative distances (mm) |",
                  "| --- | ---: | --- | --- |"])
    for label, key in (("LaunchPad", "launchPad"), ("Automation Studio", "automationStudio")):
        values = layout[key]
        distances = ", ".join(f"{value:g}" for value in values["distancesFromMasterMm"])
        lines.append(f"| {label} | {values['count']} | {text(values['master'])} | {distances} |")
    return lines


def markdown_frame(frame, text):
    lines = ["", "## Coordinate frame comparison", "", f"- State {text(frame['state'])}"]
    if "candidates" not in frame:
        return lines
    if datum := frame["datum"]:
        lines.append(f"- Translation from datum **{text(datum['label'])}** · SHA-256 {datum['snapshot']['sha256']}")
    else:
        lines.append("- Translation fitted by least squares from the paired route objects")
    lines.append(f"- Axis mapping with lowest RMS residual **{text(frame['bestRmsAxes'])}**")
    bound = frame["anyGridCompatibleTranslationLowerBoundMm"]
    lines.append(f"- Lower bound on worst object residual across all grid-compatible translations **{bound:.3f} mm**")
    if (limit := frame["positionToleranceMicrometres"]) is not None:
        lines.append(f"- Maximum object residual allowed for a reported transform {limit} micrometres")
    lines.extend(["", "| Axis mapping | Shift X / Y (mm) | RMS (mm) | Max (mm) | Worst object | Lower bound (mm) |",
                  "| --- | --- | ---: | ---: | --- | ---: |"])
    for row in frame["candidates"]:
        x, y = row["translationMm"]
        lines.append(f"| {text(row['axes'])} | {x:.3f} / {y:.3f} | {row['rmsResidualMm']:.3f} | "
                     f"{row['maxResidualMm']:.3f} | {text(row['worstObject'])} | {row['anyTranslationLowerBoundMm']:.3f} |")
    return lines


def markdown_browser(browser, text):
    lines = ["", "## Headless browser", "",
             f"- Browser state {text(browser['state'])} · Cleanup {text(browser.get('cleanup'))}"]
    if "chrome" in browser:
        lines.append(f"- Chrome {text(browser['chrome']['version'])} · URL {text(browser.get('url'))}")
        lines.append(f"- Assembly {text(browser['assemblyWidgetId'])} · Counter {text(browser['counterWidgetId'])}")
    for phase, label in (("initial", "Initial document"), ("reopened", "Reopened document")):
        if document := browser.get(phase):
            samples = ", ".join(document["counterSamples"])
            lines.append(f"- {label} · Samples {text(samples or None)} · Time origin {text(document.get('timeOrigin'))}")
    return lines


def markdown_opcua(title, opcua, text):
    lines = ["", f"## {title}", "",
             f"- Probe state {text(opcua['state'])} · Endpoint {text(opcua.get('endpoint'))}",
             f"- Namespace {text(opcua.get('namespaceUri'))} · Node {text(opcua.get('nodeId'))}",
             f"- AccessLevel {text(opcua.get('accessLevel'))} · UserAccessLevel {text(opcua.get('userAccessLevel'))}",
             f"- Client disconnected {text(opcua.get('clientDisconnected'))}"]
    for sample in opcua.get("samples", []):
        lines.append(f"- Value {text(sample['value'])} · Status 0x{sample['statusCode']:08X}"
                     f" · Source timestamp {text(sample['sourceTimestamp'])}")
    return lines


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
