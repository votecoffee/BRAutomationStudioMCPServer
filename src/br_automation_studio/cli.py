
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
    if "simulationEnvironment" in report:
        return markdown_simulation_environment(response, text)
    if "widgetPackageReview" in report:
        return markdown_widget_package_review(response, text)
    if "motionInterfaceReview" in report:
        return markdown_motion_interfaces(response, text)

    titles = {
        "project_inspect": "Saved Automation Studio project inspection",
        "assembly_io_review": "Saved ACOPOS 6D assembly I/O review",
        "layout_device_review": "Saved Assembly6D file-device review",
        "launchpad_route_review": "Saved Traffic Manager route review",
        "launchpad_station_review": "Saved Traffic Manager station review",
        "launchpad_recovery_review": "Saved Traffic Manager recovery review",
        "launchpad_motion_review": "Saved ACOPOS 6D motion review",
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
    if layout_device := report.get("layoutDeviceReview"):
        lines.extend(markdown_layout_device(layout_device, text))
    if io_review := report.get("ioReview"):
        lines.extend(markdown_io(io_review, text))
    if route := report.get("routeReview"):
        lines.extend(markdown_route(route, text))
        if stations := report.get("stationReview"):
            lines.extend(markdown_stations(stations, text))
        if recovery := report.get("recoveryReview"):
            lines.extend(markdown_recovery(recovery, text))
        if motion := report.get("motionReview"):
            lines.extend(markdown_motion(motion, text))
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
        lines.extend(markdown_simulation_prerequisites(simulation, text))
        if export := simulation["launchPadExport"]:
            lines.append(f"- LaunchPad export {text(export['path'])} · SHA-256 {text(export['sha256'])}")
    if definition := report.get("mpTrafficDefinition"):
        lines.extend(["", "## MpTraffic build verification", "", *markdown_mptraffic_definition(definition, text)])
    if declarations := report.get("motionDeclarations"):
        lines.extend(["", "## Motion IEC declarations", "", f"- State {text(declarations['state'])}"])
        for library in declarations["libraries"]:
            lines.append(f"- {text(library['name'])} {text(library.get('version'))} · {text(library['state'])} "
                         f"· Declared files {len(library['files'])}")
    if motion := report.get("motionPayloads"):
        lines.extend(["", "## Motion payload pins", "", f"- Target {text(motion['target'])}"])
        for pin in motion["pins"]:
            lines.append(f"- {text(pin['name'])} · {text(pin['version'])} · {text(pin['origin'])} "
                         f"· {text(pin['path'])} · SHA-256 {pin['sha256']}")
            if match := pin.get("matchingCatalogPath"):
                lines.append(f"  - Matching catalog payload {text(match)}")
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
            if payload := package.get("mpTrafficPayload"):
                lines.append(f"- Packaged MpTraffic {text(payload['path'])} · SHA-256 {payload['sha256']}")
            if payloads := package.get("motionPayloads"):
                lines.append(f"- Verified motion payloads {len(payloads)}")
    lines.extend(markdown_diagnostics(report["diagnostics"], text))
    return "\n".join(lines)


def markdown_motion_interfaces(response, text):
    report = response["result"]
    review = report["motionInterfaceReview"]
    lines = ["# AS4 motion function-block interfaces", "",
             f"- Project **{text(report['project'])}** · Status **{text(response['status'])}**",
             f"- mapp 6D {text(review['mapp6DSelector'])} · Build {text(review['mapp6DBuild'])}",
             f"- Interface review {text(review['state'])}"]
    for block in review["blocks"]:
        lines.extend(["", f"## {text(block['name'])}", ""])
        if block["state"] != "verified":
            lines.append(f"- {text(block['state'])}")
            continue
        source = block["source"]
        lines.extend([f"- {text(source['library'])} {text(source['version'])} · {text(source['origin'])}",
                      f"- {text(source['path'])} · Line {block['line']} · SHA-256 {source['sha256']}", "",
                      "| Pin | Direction | IEC type | Line |", "| --- | --- | --- | ---: |"])
        for pin in block["pins"]:
            lines.append(f"| {text(pin['name'])} | {text(pin['direction'])} | {text(pin['type'])} | {pin['line']} |")
    if fields := review.get("fields"):
        lines.extend(["", "## Requested STRUCT fields", "", "| Field | State | IEC type | Owner | Source line |",
                      "| --- | --- | --- | --- | --- |"])
        for row in fields:
            source = row.get("source", {})
            location = f"{source['path']}:{row['line']}" if source else None
            lines.append(f"| {text(row['path'])} | {text(row['state'])} | {text(row.get('type'))} "
                         f"| {text(row.get('owner'))} | {text(location)} |")
    lines.extend(markdown_diagnostics(report["diagnostics"], text))
    return "\n".join(lines)


def markdown_widget_package_review(response, text):
    report = response["result"]
    review = report["widgetPackageReview"]
    lines = ["# Saved mapp View widget package inventory", "",
             f"- Project **{text(report['project'])}** · Status **{text(response['status'])}**",
             f"- Review {text(review['state'])} · Widgets manifest {text(review['widgetsManifest'])}",
             f"- Read-set SHA-256 {report['snapshot']['sha256']}"]
    if build := review.get("nativeBuild"):
        lines.append(f"- Native build evidence {text(build['state'])}")
        if build["state"] == "verified":
            lines.append(f"- Build log SHA-256 {build['log']['sha256']} · RUC SHA-256 {build['ruc']['sha256']}")
    for library in review["libraries"]:
        lines.extend(["", f"## {text(library['manifest'])}", "",
                      f"- State {text(library['state'])} · Declared entries {len(library['declared'])} "
                      f"· Extra {len(library['extra'])} · Missing or wrong type {len(library['missing'])}"])
        for entry in library["extra"]:
            note = f" · AS warning {entry['builderWarning']}" if entry["builderWarning"] is not None else ""
            lines.append(f"- Undeclared {text(entry['kind'])} {text(entry['name'])}{note}")
            if evidence := entry.get("nativeEvidence"):
                matching = sum(member["savedBytesMatch"] is True for member in evidence["members"])
                lines.append(f"  - Builder warnings {len(evidence['warnings'])} · Copy steps {len(evidence['copies'])}"
                             f" · Web assets {len(evidence['members'])} · Saved bytes match {matching}")
    lines.extend(markdown_diagnostics(report["diagnostics"], text))
    return "\n".join(lines)


def markdown_diagnostics(diagnostics, text):
    lines = ["", "## Diagnostics", ""]
    for item in diagnostics:
        scope = f"{text(item['scope'])} · " if "scope" in item else ""
        lines.append(f"- {text(item['severity'])} · {scope}{text(item['path'])} · {text(item['message'])}")
    if not diagnostics:
        lines.append("No issues reported by the selected checks.")
    return lines


def markdown_simulation_prerequisites(simulation, text):
    reference = simulation["reference"]
    installer = simulation["simulatorInstallation"]
    lines = ["", "## Complete simulation prerequisites", "",
             f"- State **{text(simulation['state'])}** · Target mapp 6D {text(simulation['targetMapp6D'])}",
             f"- Expected Planar Motor Simulation Tool {text(reference['simulatorVersion'])}",
             f"- Windows Installer state {text(installer['state'])} ({text(installer['stateCode'])})"
             f" · version {text(installer['version'])} · expected {text(reference['msiVersion'])}",
             f"- Reference segment firmware {text(reference['segmentFirmware'])}"
             f" · Application data {text(reference['segmentApplicationData'])}"]
    if not simulation["installedTargetCatalog"]:
        lines.append("- No installed catalog selectors for this family")
    for candidate in simulation["installedTargetCatalog"]:
        lines.append(f"- Catalog selector {text(candidate['selector'])} · build {text(candidate['internalVersion'])}"
                     f" · manifest verified {text(candidate['verified'])}")
    if media := simulation["simulatorZip"]:
        lines.append(f"- Simulator ZIP {text(media['path'])} · SHA-256 {text(media['sha256'])}"
                     f" · matches reviewed release {text(media['matched'])}")
    if definition := simulation.get("mpTrafficDefinition"):
        lines.extend(markdown_mptraffic_definition(definition, text))
    return lines


def markdown_mptraffic_definition(definition, text):
    lines = [f"- MpTraffic definition **{text(definition['state'])}**"]
    if "installed" in definition:
        lines.append(f"- Saved mapp 6D {text(definition['mapp6DSelector'])} "
                     f"· Installed build {text(definition['mapp6DBuild'])} "
                     f"· MpTraffic {text(definition['libraryVersion'])}")
        lines.append(f"- Enum values match {text(definition['enumMatched'])} "
                     f"· IA32 binary matches {text(definition['payloadMatched'])}")
        for profile in ("saved", "installed"):
            values = ", ".join(f"{code} {label}" for code, label in definition[profile]["codes"].items())
            lines.append(f"- {profile.title()} detection values {text(values)}")
    return lines


def markdown_simulation_environment(response, text):
    report = response["result"]
    result = report["simulationEnvironment"]
    lines = ["# AS4 ACOPOS 6D simulation environment", "",
             f"- Status **{text(response['status'])}**",
             f"- Automation Studio {text(result['studioVersion'])} · {text(result['studioRoot'])}",
             f"- Catalog read-set SHA-256 {result['targetCatalogSnapshot']['sha256']}"]
    lines.extend(markdown_simulation_prerequisites(result, text))
    lines.extend(["", "## Installed technology packages", ""])
    for name, selectors in result["catalogSelectors"].items():
        values = ", ".join(selectors) or "none found"
        lines.append(f"- {text(name)} selectors · {text(values)}")
    for name, candidates in result["companionCatalog"].items():
        for item in candidates:
            lines.append(f"- {text(name)} {text(item['selector'])} · build {text(item['internalVersion'])}"
                         f" · manifest verified {text(item['verified'])}")
    lines.extend(markdown_diagnostics(report["diagnostics"], text))
    return "\n".join(lines)


def markdown_ruc(response, text):
    report = response["result"]
    review = report["rucReview"]
    smoke = report.get("arsimSmoke")
    binding = report.get("bindingReview")
    title = "# AS4 RUC review"
    if response["operation"] == "layout_device_review":
        title = "# Compiled Assembly6D file-device review"
    elif response["operation"] == "layout_svg_review":
        title = "# Generated Assembly6D SVG review"
    elif response["operation"] == "assembly_io_review":
        title = "# Compiled ACOPOS 6D assembly I/O review"
    elif response["operation"] == "arsim_hmi_smoke":
        title = "# AS4 mapp View end-to-end smoke"
    elif response["operation"] == "arsim_status_smoke":
        title = "# AS4 mapp View status reconnect"
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
    elif response["operation"] == "station_trace_review":
        title = "# Traffic Manager inspection leg trace review"
    elif response["operation"] == "cycle_trace_review":
        title = "# Traffic Manager inspection cycle trace review"
    elif response["operation"] == "launchpad_recovery_review":
        title = "# Compiled Traffic Manager recovery review"
    elif response["operation"] == "launchpad_motion_review":
        title = "# Compiled ACOPOS 6D motion review"
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
        if payloads := review.get("motionPayloads"):
            lines.append(f"- Verified motion payloads {len(payloads)}")
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
            if member := asset.get("archiveMember"):
                module = f" · Named module {text(asset['namedModule'])}" if "namedModule" in asset else ""
                lines.append(f"  - RUC member {text(member)}{module}")
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
            if capture := hmi.get("scanCapture"):
                lines.append(f"- Session scan capture {text(capture['state'])}")
                if capture["state"] == "retained":
                    lines.append(f"- {text(capture['path'])} · {capture['bytes']} bytes · SHA-256 {capture['sha256']}")
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
    if start := review.get("startPageWidget"):
        lines.extend(["", "## Default visualization start page", "",
                      f"- {text(start['state'])} · Assembly6D widget {text(start['domId'])}"])
        if saved := start.get("saved"):
            identifiers = ("visualizationId", "pageId", "layoutId", "areaId", "contentId")
            lines.append("- " + " → ".join(text(saved[key]) for key in identifiers))
        if compiled := start.get("compiled"):
            lines.append(f"- Generated client map {text(compiled['clientMap'])}")
            lines.append(f"- Generated content {text(compiled['contentUrl'])}")
    if layout_device := report.get("layoutDeviceReview"):
        lines.extend(markdown_layout_device(layout_device, text))
    if layout_svg := report.get("layoutSvgReview"):
        lines.extend(markdown_layout_svg(layout_svg, text))
    if io_review := report.get("ioReview"):
        lines.extend(markdown_io(io_review, text))
    if route := report.get("routeReview"):
        lines.extend(markdown_route(route, text))
        if stations := report.get("stationReview"):
            lines.extend(markdown_stations(stations, text))
        if recovery := report.get("recoveryReview"):
            lines.extend(markdown_recovery(recovery, text))
        if motion := report.get("motionReview"):
            lines.extend(markdown_motion(motion, text))
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
    if trace := report.get("stationTraceReview"):
        lines.extend(markdown_station_trace(trace, text))
    if cycle := report.get("cycleTraceReview"):
        lines.extend(markdown_cycle_trace(cycle, text))
    if hmi := report.get("cycleHmiReview"):
        lines.extend(markdown_cycle_hmi(hmi, text))
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


def markdown_layout_device(review, text):
    lines = ["", "## Assembly6D file device", "", f"- Device {text(review['name'])} · State {text(review['state'])}",
             f"- Saved-to-compiled path {text(review['pathComparison'])}"]
    for label, target in (("Saved", "target"), ("Compiled", "deviceParameter")):
        if row := review[label.lower()]:
            lines.append(f"- {label} {text(row['state'])} · {text(row['path'])} · {text(row.get(target))}")
    return lines


def markdown_layout_svg(review, text):
    lines = ["", "## Generated layout", "",
             f"- State {text(review['state'])} · Manager {text(review['managerId'])}"]
    if "layoutId" in review:
        lines.append(f"- Assembly {text(review['assemblyId'])} · Layout {text(review['layoutId'])}")
    for row in review["assets"].values():
        lines.append(f"- {text(row['path'])} · SHA-256 {row['sha256']}")
    if "segments" in review:
        lines.append(f"- Segments matched {len(review['segments'])}")
    if "objects" in review:
        lines.extend([f"- Directed mapping links matched {len(review['edges'])}", "",
                      "| Object | Type | Configured X/Y (mm) | SVG X/Y (mm) | Max axis delta (mm) |",
                      "| --- | --- | --- | --- | ---: |"])
        for row in review["objects"]:
            configured = ", ".join(f"{value:.6f}" for value in row["configuredMm"])
            actual = ", ".join(f"{value:.6f}" for value in row["svgMm"])
            lines.append(f"| {text(row['name'])} | {text(row['type'])} | {configured} | {actual} | "
                         f"{row['maxAxisDeltaMm']:.9f} |")
    return lines


def markdown_io(review, text):
    lines = ["", "## Assembly I/O references and read axes", "",
             f"- Assembly {text(review['assembly'])} · State {text(review['state'])}"]
    if "savedPath" in review:
        lines.append(f"- Saved {text(review['savedPath'])} · Compiled {text(review['compiledPath'])}")
    if not review["references"]:
        return lines
    lines.extend(["", "| Field | Saved | Compiled | Comparison | Saved declaration |", "| --- | --- | --- | --- | --- |"])
    for key, row in {**review["references"], **review["readAxes"]}.items():
        saved = row["saved"]
        compiled = row["compiled"]
        before = f"{text(saved['raw'])} ({text(saved['state'])})"
        after = f"{text(compiled['raw'])} ({text(compiled['state'])})" if compiled is not None else text(None)
        found = row.get("declaration")
        declaration = found["state"] if found else None
        if found and "line" in found:
            declaration = f"{found['path']} line {found['line']} · {found['type']} ({found['state']})"
        lines.append(f"| {key} | {before} | {after} | {text(row['comparison'])} | {text(declaration)} |")
    return lines


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


def markdown_station_trace(trace, text):
    lines = ["", "## Inspection leg trace", ""]
    if "state" in trace:
        lines.append(f"- State **{text(trace['state'])}**")
    if "path" in trace:
        lines.append(f"- Path {' → '.join(text(name) for name in trace['path'])}")
    if identity := trace.get("trace"):
        lines.append(f"- Trace {text(identity['path'])} · SHA-256 {identity['sha256']}")
    if "captureKind" in trace:
        lines.append(f"- Capture {text(trace['captureKind'])} · Request {text(trace['requestId'])} · Shuttle {text(trace['shuttleId'])}")
    if "releaseScans" in trace:
        scans = ", ".join(map(str, trace["releaseScans"])) or None
        lines.append(f"- Approved result scan {text(trace['acceptedResultScan'])} · Release edge scans {text(scans)}")
        lines.append(f"- Buffered scan {text(trace['bufferedScan'])} · Done scan {text(trace['doneScan'])}")
        lines.append(f"- Ignored results {trace['ignoredResults']} · Ignored detections {trace['ignoredDetections']}")
    if arrival := trace.get("arrival"):
        lines.append(f"- Arrival at {text(arrival['station'])} · Scan {arrival['scan']} · Monitor sequence {arrival['sequence']}")
    if fault := trace.get("firstFault"):
        lines.append(f"- First adapter status {fault['statusId']} at scan {fault['scan']}")
    return lines


def markdown_cycle_trace(cycle, text):
    lines = ["", "## Inspection cycle trace", "",
             f"- State **{text(cycle['state'])}** · Phase {text(cycle.get('phase'))}"]
    if trace := cycle.get("trace"):
        lines.append(f"- Trace {text(trace['path'])} · SHA-256 {trace['sha256']}")
    if capture := cycle.get("scanCapture"):
        lines.append(f"- Scan CSV SHA-256 {capture['sha256']}")
        if "records" in capture:
            lines.append(f"- CSV records {capture['records']} · Inspection scans {capture['inspectionScans']} "
                         f"· Return scans {capture['returnScans']} · Hold scan {capture['nextHoldScan']}")
        if encoding := capture.get("eventEncoding"):
            lines.append(f"- Detection enum from mapp 6D {text(encoding['mapp6DVersion'])} "
                         f"· MpTraffic {text(encoding['libraryVersion'])} · SHA-256 {encoding['typeSha256']}")
        for leg, observed in capture.get("monitorSequences", {}).items():
            lines.append(f"- {text(leg.title())} recorded monitor sequence {observed['start']} → {observed['end']}")
    if outbound := cycle.get("outbound"):
        lines.extend(markdown_station_trace(outbound, text))
    if returned := cycle.get("returnLeg"):
        lines.extend(["", "## Return leg trace", "", f"- Path {' → '.join(text(name) for name in returned['path'])}"])
        lines.append(f"- Source occupancy scan {text(returned.get('occupiedScan'))} "
                     f"· Source clear scan {text(returned.get('sourceClearScan'))}")
        if arrival := returned.get("arrival"):
            lines.append(f"- Arrival at {text(arrival['station'])} · Scan {arrival['scan']} · Monitor sequence {arrival['sequence']}")
        if fault := returned.get("firstFault"):
            lines.append(f"- First return status {fault['statusId']} at scan {fault['scan']}")
    if hold := cycle.get("nextHold"):
        lines.extend(["", "## Next inspection hold", "",
                      f"- Station {text(hold['station'])} · Shuttle {hold['shuttleId']} "
                      f"· New request {hold['requestId']} · Scan {hold['scan']}"])
    return lines


def markdown_cycle_hmi(hmi, text):
    lines = ["", "## Held status after HMI reopen", "", f"- State {text(hmi['state'])}"]
    if status_report := hmi.get("statusReport"):
        lines.append(f"- Status report SHA-256 {status_report['sha256']}")
    if "sessionId" in hmi:
        lines.append(f"- ARsim session {text(hmi['sessionId'])} · Cycle capture {text(hmi['captureKind'])}")
    if capture := hmi.get("scanCapture"):
        lines.append(f"- Session scan file matched · {capture['bytes']} bytes · SHA-256 {capture['sha256']}")
    if heartbeat := hmi.get("heartbeat"):
        lines.append(f"- Heartbeat at hold {text(heartbeat['atHold'])} "
                     f"· Last direct sample before browser {text(heartbeat.get('directBeforeLast'))}")
    for role, row in hmi.get("status", {}).items():
        lines.append(f"- {text(role)} {row['value']} · Widget {text(row['widget'])} · Node {text(row['identifier'])}")
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


def markdown_motion(motion, text):
    lines = ["", "## In-plane limits and LaunchPad exit values", "", f"- State {text(motion['state'])}"]
    if not motion["launchPadExitParameters"]:
        return lines
    lines.append(f"- Linked assembly {text(motion['assembly'])}")
    if "savedPath" in motion:
        lines.append(f"- Saved {text(motion['savedPath'])} · Compiled {text(motion['compiledPath'])}")
    if motion["limits"]:
        lines.extend(["", "| Limit | Unit | Saved | Compiled | Comparison |", "| --- | --- | --- | --- | --- |"])
        for key, row in motion["limits"].items():
            before, after = row["saved"], row["compiled"]
            saved = f"{text(before['raw'])} ({text(before['state'])})"
            compiled = f"{text(after['raw'])} ({text(after['state'])})" if after is not None else text(None)
            lines.append(f"| {key} | {row['unit']} | {saved} | {compiled} | {text(row['comparison'])} |")
    lines.extend(["", "| Route object | Type | Raw exit velocity | Raw exit acceleration | Raw exit payload weight |",
                  "| --- | --- | --- | --- | --- |"])
    for row in motion["launchPadExitParameters"]:
        lines.append(f"| {text(row['importedName'])} | {text(row['type'])} | {text(row['exit_velocity'])} | "
                     f"{text(row['exit_acceleration'])} | {text(row['exit_payload_weight'])} |")
    return lines


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
        if assembly := browser["assemblyWidgetId"]:
            lines.append(f"- Assembly {text(assembly)} · Counter {text(browser['counterWidgetId'])}")
        else:
            lines.append(f"- Counter {text(browser['counterWidgetId'])}")
    for phase, label in (("initial", "Initial document"), ("reopened", "Reopened document")):
        if document := browser.get(phase):
            samples = ", ".join(document["counterSamples"])
            lines.append(f"- {label} · Samples {text(samples or None)} · Time origin {text(document.get('timeOrigin'))}")
            for widget, value in document.get("statusValues", {}).items():
                lines.append(f"  - {text(widget)} · Value {text(value)}")
            if bundle := document.get("widgetBundle"):
                lines.append(f"  - Widget bundle {text(bundle['state'])} · {text(bundle['path'])}"
                             f" · HTTP {text(bundle['httpStatus'])}")
            if client_map := document.get("startPageMap"):
                lines.append(f"  - Start page map {text(client_map['state'])} · {text(client_map['path'])}"
                             f" · HTTP {text(client_map['httpStatus'])}")
            if layout := document.get("layout"):
                lines.append(f"  - Layout {text(layout['state'])} · Inline SVGs with layout bounds {text(layout['boundedSvgCount'])}")
                for role, asset in layout["assets"].items():
                    lines.append(f"  - {text(role)} {text(asset['path'])} · HTTP {text(asset['httpStatus'])}")
    if capture := browser.get("layoutCapture"):
        lines.append(f"- Layout capture {text(capture['state'])} · Paths relative to the session directory")
        for asset in capture["assets"].values():
            lines.append(f"  - {text(asset['path'])} · {text(asset['contentType'])} · {asset['bytes']} bytes · "
                         f"SHA-256 {asset['sha256']} · {text(asset['file'])}")
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
    for identifier, status in opcua.get("statusValues", {}).items():
        lines.append(f"- {text(identifier)} · Value {text(status.get('value'))} · Type {text(status.get('variantType'))}"
                     f" · UserAccessLevel {text(status.get('userAccessLevel'))}")
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
