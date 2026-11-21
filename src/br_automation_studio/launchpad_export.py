"""Read the LaunchPad export groups observed in the AS4 2.2 importer."""

from hashlib import sha256
from xml.etree import ElementTree as ET

from .launchpad_baseline import compare_cycle, nonempty, read_launchpad_save, records, route_baseline
from .project import ProjectXml, SavedFiles, XML_LIMIT


GROUPS = {
    "Segments": None,
    "Stations": "NbOfStations",
    "Waypoints": "NbOfWaypoints",
    "Gates": "NbOfGates",
    "Branches": "NbOfBranches",
    "ExclusiveGroups": "NbOfExclusiveGroups",
}
OBJECT_TYPES = {"Stations": "Station", "Waypoints": "Waypoint", "Gates": "Waypoint", "Branches": "Waypoint"}
DESTINATION_TYPES = {
    "NextStations": "Station", "NextWaypoints": "Waypoint", "NextGates": "Waypoint", "NextBranches": "Waypoint",
}


def export_bytes(path):
    selected = path.resolve()
    if path.suffix.casefold() != ".x6d" or selected.suffix.casefold() != ".x6d":
        raise ValueError("Select a LaunchPad .x6d file")
    with selected.open("rb") as stream:
        data = stream.read(XML_LIMIT + 1)
    if len(data) > XML_LIMIT:
        raise ValueError(f"LaunchPad export inspection is limited to {XML_LIMIT} bytes")
    return data, {"path": str(selected), "bytes": len(data), "sha256": sha256(data).hexdigest()}


def export_groups(data):
    try:
        root = ET.fromstring(data, parser=ET.XMLParser(target=ProjectXml()))
    except (ET.ParseError, LookupError) as error:
        raise ValueError(f"Could not read the export as XML: {error}") from error

    groups = {}
    other = []
    for group in root.findall("Group"):
        identifier = group.get("ID")
        if identifier not in GROUPS:
            other.append(identifier)
            continue
        if identifier in groups:
            raise ValueError(f"Multiple top-level LaunchPad groups named {identifier}")
        counter = GROUPS[identifier]
        groups[identifier] = [item for item in group.findall("Group") if counter is None or item.get("ID") != counter]
    if "Segments" not in groups:
        raise ValueError("Could not find the unqualified top-level Segments group read by the AS4 2.2 importer")
    return root.tag, groups, other


def named_entry(node):
    names = [prop.get("Value") for prop in node.findall("Property") if prop.get("ID") == "Name"]
    return {"id": node.get("ID"), "name": names[0] if len(names) == 1 else None}


def inventory_export(path, expected_sha256):
    data, snapshot = export_bytes(path)
    if snapshot["sha256"] != expected_sha256:
        raise ValueError("The LaunchPad export changed after fingerprinting")
    root, groups, other = export_groups(data)
    return {
        "state": "read",
        "reference": "as4_mapp6d_2.2_launchpad_groups",
        "root": root,
        "groups": [{"id": key, "count": len(items), "entries": list(map(named_entry, items))}
                   for key, items in groups.items()],
        "otherGroupIds": other,
    }


def export_graph(groups):
    by_id = {}
    names = set()
    for group, kind in OBJECT_TYPES.items():
        for node in groups.get(group, ()):
            row = named_entry(node)
            identity, name = row["id"], row["name"]
            if not nonempty(identity) or not nonempty(name) or identity in by_id or name in names:
                raise ValueError("Traffic objects need unique IDs and names in the observed export groups")
            by_id[identity] = (node, {"name": name, "type": kind})
            names.add(name)

    edges, unresolved = [], []
    successors_seen = False
    for node, source in by_id.values():
        declared = set()
        for group in node.findall("Group"):
            group_id = group.get("ID")
            expected = DESTINATION_TYPES.get(group_id)
            if expected is None:
                continue
            if group_id in declared:
                raise ValueError(f"Export object {source['name']!r} repeats the {group_id} group")
            declared.add(group_id)
            successors_seen = True
            for point in group:
                destination_id = point.get("Value")
                found = by_id.get(destination_id)
                if found is None or found[1]["type"] != expected:
                    unresolved.append({"source": source["name"], "destinationId": destination_id,
                                       "expectedType": expected, "actualType": found[1]["type"] if found else None})
                else:
                    target = found[1]
                    edges.append({"source": source["name"], "sourceType": source["type"],
                                  "destination": target["name"], "destinationType": target["type"]})
    if not successors_seen:
        raise ValueError("Could not find any of the observed NextStations, NextWaypoints, NextGates or NextBranches groups")
    return {"objects": [row for _, row in by_id.values()], "edges": edges}, unresolved


def review_launchpad_export(lpj, readback, export):
    reader = SavedFiles(export.parent)
    review = {"state": "unverified", "launchPad": None, "export": None}
    report = {"evidence": "launchpad_export_readback", "exportReview": review, "diagnostics": reader.diagnostics}
    try:
        proof, decoded = read_launchpad_save(lpj, readback)
        review["launchPad"], objects = route_baseline(proof, decoded, False)
        expected_segments = len(records(decoded, "segments"))
    except (OSError, RuntimeError, ValueError) as error:
        reader.issue("error", "launchpad_export_baseline_invalid", readback, str(error))
        return report
    try:
        data, review["export"] = export_bytes(export)
    except (OSError, RuntimeError, ValueError) as error:
        reader.issue("error", "launchpad_export_unreadable", export, str(error))
        return report
    try:
        root, groups, _ = export_groups(data)
        segment_count = len(groups["Segments"])
        review.update(root=root, expectedSegments=expected_segments, exportSegments=segment_count)
        graph, unresolved = export_graph(groups)
    except ValueError as error:
        reader.issue("warning", "launchpad_export_route_unverified", export, str(error))
        return report

    comparison = compare_cycle(objects, {row["name"]: row["name"] for row in objects}, graph)
    review.update(comparison, unresolvedReferences=unresolved)
    if expected_segments != segment_count:
        reader.issue("error", "launchpad_export_segment_count", export, "Export segment count differs from the saved design")
    if comparison["missingObjects"]:
        reader.issue("error", "launchpad_export_object_mismatch", export, "Saved route objects are missing or differently typed")
    if comparison["missingEdges"] or comparison["unexpectedEdges"]:
        reader.issue("error", "launchpad_export_edge_mismatch", export, "Export edges differ from the saved directed cycle")
    if unresolved:
        reader.issue("error", "launchpad_export_reference_unresolved", export, "Export successors have unresolved IDs or types")
    if comparison["additionalObjects"]:
        reader.issue("warning", "launchpad_export_additional_objects", export, "Review extra traffic objects in the export")
    severities = {row["severity"] for row in reader.diagnostics}
    review["state"] = "issues" if "error" in severities else "review_required" if severities else "matched"
    return report
