"""Read the source-bound LaunchPad save and compare a single typed cycle."""

from collections import Counter
from hashlib import file_digest, sha256

from .json_io import decode_json
from .launchpad_motion import read_model_exit
from .launchpad_stations import model_process


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
        ordered.append(current)
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


def read_launchpad_save(lpj, readback):
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
    with lpj.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    if origin.get("sha256") != digest:
        raise ValueError("The selected .lpj SHA-256 differs from the source identified by the readback")
    version = decoded.get("launchpad_version")
    if not nonempty(version):
        raise ValueError("The decoded save has no LaunchPad version")
    proof = {"sourcePath": str(lpj.resolve()), "sourceSha256": digest,
             "readbackPath": readback_file["path"], "readbackSha256": readback_file["sha256"],
             "decodeSourceName": origin["fileName"], "launchPadVersion": version, "codec": payload["codec"]}
    return proof, decoded


def route_baseline(proof, decoded, include_geometry, *, include_stations=False, include_motion=False):
    layout = layout_objects(decoded)
    ordered = simple_cycle(decoded, layout)
    proof["unusedGeometry"] = sorted(layout[ident]["name"] for ident in layout.keys() - {row["id"] for row in ordered})
    objects = []
    for node in ordered:
        ident = node["id"]
        row = layout[ident]
        obj = {"name": row["name"], "type": row["type"], "geometryId": ident}
        if include_geometry:
            obj["launchPadXY"] = row["xy"]
        if include_stations and row["type"] == "Station":
            obj["modelProcess"] = model_process(node)
        if include_motion:
            obj["modelExit"] = read_model_exit(node)
        objects.append(obj)
    return proof, objects


def baseline(lpj, readback, *, include_geometry=False):
    return route_baseline(*read_launchpad_save(lpj, readback), include_geometry)


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
