"""Compare a selected AS4 manager with a source-bound saved LaunchPad cycle."""

from collections import Counter
from hashlib import sha256
from pathlib import Path

from .inspection import inspect_project
from .json_io import decode_json
from .launchpad_geometry import measure_geometry
from .native_build import fingerprint
from .ruc_review import review_ruc

READBACK_LIMIT = 16 * 1024 * 1024
EDGE_KEYS = ("source", "sourceType", "destination", "destinationType")
LAYOUT_TYPES = {"workstations": ("Station", "workstation"), "gates": ("Waypoint", "gate")}
RETURN_MARKER = "recipe_loop_merge_node"


def nonempty(value):
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def records(document, key):
    items = document.get(key)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"Expected a {key} object list in the decoded LaunchPad save")
    return items


def layout_objects(decoded):
    layout = {}
    names = set()
    for key, (kind, recipe_type) in LAYOUT_TYPES.items():
        for row in records(decoded, key):
            ident, name = row.get("id"), row.get("name")
            if not nonempty(ident) or not nonempty(name):
                raise ValueError("Every saved station and gate needs a geometry ID and name")
            if ident in layout or name in names:
                raise ValueError(f"Duplicate saved geometry ID or name at {name!r}")
            names.add(name)
            layout[ident] = {"name": name, "type": kind, "recipeType": recipe_type, "xy": [row.get("x"), row.get("y")]}
    return layout


def simple_cycle(decoded, layout):
    recipe = {}
    for row in records(decoded, "recipe_nodes"):
        ident, geom = row.get("recipe_id"), row.get("id")
        if not nonempty(ident) or ident in recipe or not nonempty(geom) or geom not in layout:
            raise ValueError("Recipe IDs must be unique and every node must resolve to a saved station or gate")
        if row.get("type_name") != layout[geom]["recipeType"]:
            raise ValueError(f"Recipe node type disagrees with saved geometry for {layout[geom]['name']!r}")
        recipe[ident] = row

    terminal = [node for node in recipe.values() if node.get("name") == RETURN_MARKER]
    if len(terminal) != 1 or terminal[0].get("targets") != []:
        raise ValueError("Expected one terminal LaunchPad return marker with no outgoing targets")
    stop = terminal[0]
    start = [row for row in recipe.values() if row is not stop and row["id"] == stop["id"]]
    if len(start) != 1:
        raise ValueError("The return marker must share the geometry ID of one starting node")

    ordered = []
    seen = set()
    current = start[0]
    while current is not stop:
        geom = current["id"]
        if geom in seen:
            raise ValueError("The recipe revisits a geometry object before the return marker")
        seen.add(geom)
        ordered.append(geom)
        targets = current.get("targets")
        if (not isinstance(targets, list) or len(targets) != 1
                or not nonempty(targets[0]) or targets[0] not in recipe):
            raise ValueError("Every route node must have one successor in the saved recipe")
        current = recipe[targets[0]]
    if len(ordered) < 2 or len(ordered) + 1 != len(recipe):
        raise ValueError("Expected a single complete cycle with at least two distinct route objects")
    return ordered


def json_snapshot(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"JSON input exceeds {limit} bytes")
    return decode_json(raw), {"path": str(path.resolve()), "sha256": sha256(raw).hexdigest()}


def baseline(lpj, readback, *, include_geometry=False):
    if lpj.suffix.lower() != ".lpj" or readback.suffix.lower() != ".json":
        raise ValueError("Select the source .lpj and its decoded JSON readback")
    payload, readback_file = json_snapshot(readback, READBACK_LIMIT)
    if not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
        raise ValueError("Select a schemaVersion 1 LaunchPad readback made by the local decoder")
    if payload.get("codec") != {"name": "zipson", "version": "0.2.12"}:
        raise ValueError("The readback must identify the reviewed Zipson codec version")
    origin, decoded = payload.get("source"), payload.get("decoded")
    if not isinstance(origin, dict) or not nonempty(origin.get("fileName")) or not isinstance(decoded, dict):
        raise ValueError("The readback needs a source identity and decoded LaunchPad object")
    if not lpj.is_file():
        raise ValueError("The selected .lpj must be a readable file")
    digest = fingerprint(lpj)
    if origin.get("sha256") != digest:
        raise ValueError("The selected .lpj SHA-256 differs from the source identified by the readback")
    version = decoded.get("launchpad_version")
    if not nonempty(version):
        raise ValueError("The decoded save has no LaunchPad version")
    layout = layout_objects(decoded)
    ordered = simple_cycle(decoded, layout)
    proof = {"sourcePath": str(lpj.resolve()), "sourceSha256": digest,
             "readbackPath": readback_file["path"], "readbackSha256": readback_file["sha256"],
             "decodeSourceName": origin["fileName"], "launchPadVersion": version, "codec": payload["codec"],
             "unusedGeometry": sorted(layout[ident]["name"] for ident in set(layout) - set(ordered))}
    objects = [{"name": layout[ident]["name"], "type": layout[ident]["type"], "geometryId": ident}
               for ident in ordered]
    if include_geometry:
        for obj in objects:
            obj["launchPadXY"] = layout[obj["geometryId"]]["xy"]
    return proof, objects


def names_for(objects, name_map):
    names = {row["name"] for row in objects}
    config, snapshot = json_snapshot(name_map, 1024 * 1024) if name_map is not None else ({}, None)
    if not isinstance(config, dict) or any(key not in names or not nonempty(value) for key, value in config.items()):
        raise ValueError("Map only saved cycle names to nonempty imported names")
    renamed = {name: config.get(name, name) for name in names}
    if len(set(renamed.values())) != len(renamed):
        raise ValueError("Each saved route object must map to a different imported name")
    return renamed, snapshot


def edge_rows(counter):
    return [dict(zip(EDGE_KEYS, edge), count=count) for edge, count in counter.items()]


def compare_cycle(objects, renamed, manager):
    expected = [(renamed[row["name"]], row["type"]) for row in objects]
    actual = {(row["name"], row["type"]) for row in manager["objects"]}
    expected_types = set(expected)
    unexpected = [row for row in manager["objects"] if (row["name"], row["type"]) not in expected_types]
    missing = [dict(name=name, type=kind) for name, kind in expected if (name, kind) not in actual]
    cycle = expected[1:] + expected[:1]
    want = Counter((*start, *end) for start, end in zip(expected, cycle))
    got = Counter(tuple(row[field] for field in EDGE_KEYS) for row in manager["edges"])
    return {"expectedObjects": [{"name": row["name"], "type": row["type"], "geometryId": row["geometryId"],
                                 "importedName": renamed[row["name"]]} for row in objects],
            "missingObjects": missing, "additionalObjects": unexpected,
            "expectedEdges": edge_rows(want), "missingEdges": edge_rows(want - got),
            "unexpectedEdges": edge_rows(got - want)}


def launchpad_route_review(project, configuration, cpu, manager_id, lpj, readback, name_map=None, job_dir=None,
                           *, geometry=None):
    geometry_manager = manager_id if geometry is not None else None
    if job_dir is not None:
        report = review_ruc(project, job_dir, geometry_manager=geometry_manager)
    else:
        report = inspect_project(project, configuration, cpu, geometry_manager=geometry_manager)
    route = report["routeReview"] = {"state": "not_checked", "managerId": manager_id,
                                     "view": "compiled" if job_dir is not None else "saved"}
    if geometry is not None:
        report["geometryReview"] = {"state": "not_checked"}

    def issue(code, path, message, severity="error", scope="launchpad_route"):
        report["diagnostics"].append({"code": code, "path": path.as_posix(), "message": message,
                                      "severity": severity, "scope": scope})

    try:
        proof, objects = baseline(lpj, readback, include_geometry=geometry is not None)
        route["launchPad"] = proof
    except (OSError, RuntimeError, ValueError) as error:
        issue("launchpad_baseline_invalid", readback, str(error))
        return report
    try:
        renamed, mapping_file = names_for(objects, name_map)
        if mapping_file is not None:
            route["nameMap"] = mapping_file
    except (OSError, RuntimeError, ValueError) as error:
        issue("launchpad_name_map_invalid", name_map, str(error))
        return report

    phase = report if job_dir is None else report["rucReview"]
    for argument, selected in ((configuration, report["selectedConfiguration"]), (cpu, report["selectedCpu"])):
        if job_dir is not None and argument is not None and argument.casefold() != (selected or "").casefold():
            issue("route_selection_mismatch", project, "The requested configuration or CPU differs from the retained build")
            return report
    managers = [row for row in phase["trafficManagers"] if row["manager"] == manager_id]
    if len(managers) != 1:
        issue("route_manager_unavailable", project, f"Could not read exactly one Traffic Manager {manager_id!r}")
        return report
    manager, = managers
    route["assembly"] = manager["assembly"]
    route["objectLists"] = manager["objectLists"]
    route["mappingLists"] = manager["mappingLists"]
    comparison = compare_cycle(objects, renamed, manager)
    route.update(comparison)
    if comparison["missingObjects"]:
        issue("launchpad_object_mismatch", project, "Expected route objects are absent or have different types")
    if comparison["missingEdges"] or comparison["unexpectedEdges"]:
        issue("launchpad_edge_mismatch", project, "Directed edge names, endpoint types or multiplicities differ from the saved LaunchPad cycle")
    if comparison["additionalObjects"]:
        issue("launchpad_additional_objects", project, "Review additional or differently typed objects in the selected manager", "warning")

    severities = {item["severity"] for item in report["diagnostics"]}
    if "error" in severities:
        route["state"] = "issues"
    elif "warning" in severities:
        route["state"] = "review_required"
    else:
        route["state"] = "matched"
        report["evidence"] = "launchpad_route_compiled" if job_dir is not None else "launchpad_route_saved"

    if geometry is not None:
        try:
            measures, issues = measure_geometry(objects, renamed, manager["positionEvidence"], geometry)
        except (TypeError, ValueError, OverflowError) as error:
            report["geometryReview"]["state"] = "unverified"
            issue("launchpad_geometry_unverified", project, str(error), scope="launchpad_geometry")
        else:
            report["geometryReview"] = measures
            for code, message in issues:
                issue(code, project, message, scope="launchpad_geometry")
            if not issues and route["state"] == "matched":
                report["evidence"] = "launchpad_geometry_compiled" if job_dir is not None else "launchpad_geometry_saved"
    return report
