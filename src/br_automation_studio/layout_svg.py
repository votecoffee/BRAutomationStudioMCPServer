"""Compare retained AS4 Assembly6D SVG geometry with its saved and compiled configuration."""

from collections import Counter
from math import hypot, isfinite
from pathlib import Path
import re

from .launchpad_geometry import point
from .launchpad_layout import GRID_MM, linked_layout, master_offsets, native_segments
from .project import SavedFiles
from .ruc_review import review_ruc

SVG = "{http://www.w3.org/2000/svg}"
OBJECTS = {"Station": ("station", "rect"), "Waypoint": ("waypoint", "ellipse")}
TOLERANCE_MM = 0.001
ARROW_CLEARANCE_MM = 10.0


class UnsupportedLayout(ValueError):
    """The source or generated SVG uses a form without a verified coordinate mapping."""


def one(parent, tag, predicate, label):
    matches = [node for node in parent.findall(SVG + tag) if predicate(node)]
    if len(matches) != 1:
        raise ValueError(f"Expected one {label}, found {len(matches)}")
    return matches[0]


def classes(node):
    return (node.get("class") or "").split()


def svg_group(parent, identifier, kind):
    return one(parent, "g", lambda node: node.get("id") == identifier and kind in classes(node),
               f"{kind} group {identifier!r}")


def inventory(label, expected, observed):
    want, found = Counter(expected), Counter(observed)
    if want != found:
        raise ValueError(f"{label} differ. Missing {list((want - found).items())[:5]!r}. "
                         f"Unexpected {list((found - want).items())[:5]!r}.")


def svg_input(path, identity):
    reader = SavedFiles(path.parent)
    name = Path(path.name)
    root, _ = reader.xml(name, "svg")
    if root is None:
        raise ValueError(reader.diagnostics[0]["message"])
    if root.tag != SVG + "svg" or root.get("id") != identity + "-svg":
        raise ValueError(f"Expected a namespaced SVG root for {identity!r}")
    if "transform" in root.attrib:
        raise UnsupportedLayout("Expected a generated SVG without a root transform")
    return root, {"path": path.as_posix(), "sha256": reader.sources[name.as_posix()]}


def translation(node):
    match = re.fullmatch(r"translate\(\s*([^,\s()]+)(?:\s*,\s*|\s+)([^,\s()]+)\s*\)",
                         node.get("transform", ""))
    if not match:
        raise UnsupportedLayout(f"Expected a planar translation for {node.get('id')!r}")
    return point(match.groups(), 1)


def native_grid(layout):
    origin = layout["userOriginRaw"]
    if origin is None or None in origin or point(origin, 1) != (0, 0):
        raise UnsupportedLayout("The verified SVG coordinate mapping requires an explicit zero UserOrigin")
    try:
        segments = native_segments(layout)
        master_offsets(segments, "Automation Studio")
    except ValueError as error:
        raise UnsupportedLayout(str(error)) from error
    return {(item.xy_mm, item.master) for item in segments}


def check_assembly(root, assembly, layout_id, expected):
    assembly_group = svg_group(root, assembly, "assembly")
    grid = svg_group(assembly_group, layout_id, "layout")
    if grid.get("grid-size") != str(GRID_MM):
        raise ValueError(f"Expected the generated {GRID_MM} mm grid")
    if "transform" in assembly_group.attrib or "transform" in grid.attrib:
        raise UnsupportedLayout("The assembly and layout groups must share the SVG coordinate frame")
    actual, ids = [], []
    for node in grid.findall(SVG + "g"):
        ident = node.get("segment-id") or ""
        if ("segment" not in classes(node) or not re.fullmatch(r"[1-9][0-9]*", ident)
                or node.get("id") != f"segment-{ident}" or node.get("master") not in {"true", "false"}):
            raise ValueError("A generated segment has an invalid identity or master flag")
        ids.append(ident)
        xy = point((node.get("position-x-mm"), node.get("position-y-mm")), 1)
        cells = point((node.get("position-x"), node.get("position-y")), GRID_MM)
        shape_group = one(node, "g", lambda item: True, "segment shape group")
        rect = one(shape_group, "rect", lambda item: True, "segment rectangle")
        if ("transform" in shape_group.attrib or "transform" in rect.attrib
                or point((rect.get("width"), rect.get("height")), 1) != (GRID_MM, GRID_MM)
                or point((rect.get("x", "0"), rect.get("y", "0")), 1) != (0, 0)
                or cells != xy or translation(node) != (xy[0], -xy[1] - GRID_MM)):
            raise ValueError(f"Segment {ident} geometry differs from its declared grid position")
        actual.append((xy, node.get("master") == "true"))
    if len(set(ids)) != len(ids):
        raise ValueError("Generated segment IDs repeat")
    inventory("Segment position and master flag", expected, actual)
    return [{"originMm": xy, "master": master} for xy, master in sorted(actual)]


def native_positions(rows):
    result = {}
    for row in rows:
        name, kind = row["name"], row["type"]
        if kind not in OBJECTS or row["mode"] != "Absolute" or None in row["xy"] or name in result:
            raise UnsupportedLayout(f"Object {name!r} needs one absolute Station or Waypoint position")
        result[name] = kind, point(row["xy"], 1000)
    return result


def directed_ids(edges):
    pairs = [(edge["source"], edge["destination"]) for edge in edges]
    result = {f"mapping_{start}_to_{end}": (start, end) for start, end in pairs}
    if len(result) != len(pairs) or any(start == end or "_to_" in start or "_to_" in end for start, end in pairs):
        raise UnsupportedLayout("Expected distinct non-self mappings with unambiguous endpoint IDs")
    if set(pairs) & {(end, start) for start, end in pairs}:
        raise UnsupportedLayout("Reciprocal mappings are outside the verified one-way SVG format")
    return result


def marker_geometry(group, tag):
    x, y = translation(group)
    shape = one(group, tag, lambda item: True, "traffic marker")
    if "transform" in shape.attrib:
        raise UnsupportedLayout("Expected an untransformed traffic marker within its translated group")
    if tag == "rect":
        width, height = point((shape.get("width"), shape.get("height")), 1)
        dx, dy = point((shape.get("x", "0"), shape.get("y", "0")), 1)
        if min(width, height) <= 0:
            raise ValueError("Expected positive station rectangle dimensions")
        return point((x + dx + width / 2, -(y + dy + height / 2)), 1), (width / 2, height / 2)
    radii = point((shape.get("rx"), shape.get("ry")), 1)
    if min(radii) <= 0:
        raise ValueError("Expected positive waypoint ellipse radii")
    return point((x + float(shape.get("cx", "0")), -(y + float(shape.get("cy", "0")))), 1), radii


def boundary_distance(unit, kind, half_size):
    if min(half_size) <= 0:
        raise UnsupportedLayout("Expected positive dimensions for mapping endpoint clipping")
    ratios = tuple(abs(direction) / extent for direction, extent in zip(unit, half_size))
    return 1 / (max(ratios) if kind == "Station" else hypot(*ratios))


def mapping_line_endpoints(group, line, start, end):
    identity = group.get("id")
    if "transform" in group.attrib or "transform" in line.attrib:
        raise UnsupportedLayout(f"Expected an untransformed mapping line for {identity!r}")
    start_kind, start_xy, start_size = start
    end_kind, end_xy, end_size = end
    delta = tuple(after - before for before, after in zip(start_xy, end_xy))
    span = hypot(*delta)
    if not isfinite(span) or span == 0:
        raise UnsupportedLayout(f"Expected separated mapping endpoints for {identity!r}")
    unit = tuple(axis / span for axis in delta)
    source_clip = boundary_distance(unit, start_kind, start_size)
    target_clip = boundary_distance(unit, end_kind, end_size) + ARROW_CLEARANCE_MM
    if span <= source_clip + target_clip:
        raise UnsupportedLayout(f"Mapping {identity!r} has insufficient space for the verified line clipping")
    expected_start = tuple(axis + direction * source_clip for axis, direction in zip(start_xy, unit))
    expected_end = tuple(axis - direction * target_clip for axis, direction in zip(end_xy, unit))
    x1, y1 = point((line.get("x1"), line.get("y1")), 1)
    x2, y2 = point((line.get("x2"), line.get("y2")), 1)
    actual_start, actual_end = (x1, -y1), (x2, -y2)
    largest = max(abs(expected - actual)
                  for expected, actual in zip((*expected_start, *expected_end), (*actual_start, *actual_end)))
    if not isfinite(largest):
        raise ValueError(f"Mapping {identity!r} endpoint difference exceeds the numeric range")
    return {"expectedFromMm": expected_start, "expectedToMm": expected_end, "svgFromMm": actual_start, "svgToMm": actual_end,
            "maxAxisDeltaMm": round(largest, 9), "withinTolerance": largest <= TOLERANCE_MM}


def check_traffic(root, manager_id, positions, mappings, *, verify_mapping_line_endpoints=False):
    overlay = svg_group(root, manager_id, "traffic-manager")
    if "transform" in overlay.attrib:
        raise UnsupportedLayout("The Traffic Manager group must share the SVG coordinate frame")
    groups = overlay.findall(SVG + "g")
    expected = {f"{OBJECTS[kind][0]}_{name}": (name, kind, xy) for name, (kind, xy) in positions.items()}
    semantic = {prefix for prefix, _ in OBJECTS.values()} | {"link", "branching_waypoint"}
    relevant = [node for node in groups if semantic.intersection(classes(node)) or node.get("id")]
    inventory("Traffic object and mapping IDs", [*expected, *mappings], [node.get("id") for node in relevant])
    by_id = {node.get("id"): node for node in relevant}
    objects, edges, footprints = [], [], {}
    for identity, (name, kind, xy) in sorted(expected.items()):
        css, tag = OBJECTS[kind]
        group = by_id[identity]
        if css not in classes(group):
            raise ValueError(f"Expected a {css} group for {name!r}")
        actual, half_size = marker_geometry(group, tag)
        footprints[name] = (kind, xy, half_size)
        residual = max(abs(before - after) for before, after in zip(xy, actual))
        if not isfinite(residual):
            raise ValueError(f"Object {name!r} position difference exceeds the numeric range")
        objects.append({"name": name, "type": kind, "configuredMm": xy, "svgMm": actual,
                        "maxAxisDeltaMm": round(residual, 9), "withinTolerance": residual <= TOLERANCE_MM})
    for identity, (start, end) in sorted(mappings.items()):
        group = by_id[identity]
        css = set(classes(group))
        direction = {"gray-one-way", "white-one-way", "gray-two-way", "white-two-way"} & css
        if "link" not in css or direction not in ({"gray-one-way"}, {"white-one-way"}):
            raise ValueError(f"Expected a one-way generated link {identity!r}")
        line = one(group, "line", lambda item: True, "mapping line")
        edge = {"source": start, "destination": end}
        if verify_mapping_line_endpoints:
            edge["lineEndpoints"] = mapping_line_endpoints(group, line, footprints[start], footprints[end])
        edges.append(edge)
    return objects, edges


def layout_svg_review(project, job, manager_id, assembly_svg, traffic_svg, *, verify_mapping_line_endpoints=False):
    report = review_ruc(project, job, geometry_manager=manager_id, include_layout=True, include_saved_geometry=True)
    ruc = report["rucReview"]
    result = report["layoutSvgReview"] = {"state": "not_checked", "managerId": manager_id,
                                          "positionToleranceMm": TOLERANCE_MM, "assets": {}}
    if verify_mapping_line_endpoints:
        result["mappingLineEndpoints"] = {"state": "not_checked", "arrowClearanceMm": ARROW_CLEARANCE_MM}

    def issue(code, path, message, severity="error"):
        report["diagnostics"].append({"scope": "layout_svg", "path": path.as_posix(),
                                      "code": code, "message": message, "severity": severity})

    if report["diagnostics"] or not (ruc["sourceMatched"] and ruc["archive"]):
        return report

    path = project
    try:
        managers = [row for row in ruc["trafficManagers"] if row["manager"] == manager_id]
        if len(managers) != 1:
            raise ValueError(f"Expected exactly one compiled Traffic Manager {manager_id!r}")
        manager, = managers
        assembly = result["assemblyId"] = manager["assembly"]
        layout = linked_layout(ruc["assemblyLinks"], assembly)
        result["layoutId"] = layout["layoutId"]
        saved = linked_layout(ruc["savedAssemblyLinks"], assembly)
        grid = native_grid(layout)
        if native_grid(saved) != grid:
            raise ValueError("Saved and compiled segment positions or master flags differ")
        positions = native_positions(manager["positionEvidence"])
        if native_positions(ruc["savedPositionEvidence"]) != positions:
            raise ValueError("Saved and compiled traffic object positions differ")
        mappings = directed_ids(manager["edges"])
        path = assembly_svg
        root, result["assets"]["assembly"] = svg_input(path, assembly)
        result["segments"] = check_assembly(root, assembly, layout["layoutId"], grid)
        path = traffic_svg
        root, result["assets"]["trafficManager"] = svg_input(path, manager_id)
        result["objects"], result["edges"] = check_traffic(
            root, manager_id, positions, mappings, verify_mapping_line_endpoints=verify_mapping_line_endpoints,
        )
        for item in result["objects"]:
            if not item["withinTolerance"]:
                issue("layout_svg_position_mismatch", path,
                      f"{item['name']} differs by {item['maxAxisDeltaMm']} mm on its largest axis")
        if verify_mapping_line_endpoints:
            failed = 0
            edges = result["edges"]
            for edge in edges:
                endpoint = edge["lineEndpoints"]
                if not endpoint["withinTolerance"]:
                    failed += 1
                    issue("layout_svg_line_endpoint_mismatch", path,
                          f"{edge['source']} to {edge['destination']} line endpoints differ by up to "
                          f"{endpoint['maxAxisDeltaMm']} mm")
            result["mappingLineEndpoints"].update(
                state="issues" if failed else "matched", checkedLinks=len(edges), failedLinks=failed,
                maxAxisDeltaMm=max((edge["lineEndpoints"]["maxAxisDeltaMm"] for edge in edges), default=None),
            )
    except UnsupportedLayout as error:
        issue("layout_svg_unverified", path, str(error), "warning")
        result["state"] = "unverified"
        return report
    except (ValueError, TypeError, OverflowError) as error:
        issue("layout_svg_mismatch", path, str(error))
    if report["diagnostics"]:
        result["state"] = "issues"
    else:
        result["state"] = "matched"
        report["evidence"] = "layout_svg_readback"
    return report
