"""Compare a square S3 segment layout relative to its declared master."""

from dataclasses import dataclass
from fractions import Fraction
from math import hypot, isfinite
import re


GRID_MM = 240
SEGMENT_TYPE = "S3-AS"
AXES = {
    "x, y": (1, 0, 0, 1),
    "-y, x": (0, -1, 1, 0),
    "-x, -y": (-1, 0, 0, -1),
    "y, -x": (0, 1, -1, 0),
    "x, -y": (1, 0, 0, -1),
    "-x, y": (-1, 0, 0, 1),
    "y, x": (0, 1, 1, 0),
    "-y, -x": (0, -1, -1, 0),
}


@dataclass(frozen=True)
class Segment:
    label: str
    xy_mm: tuple[int | Fraction, int | Fraction]
    master: bool


def launchpad_segments(decoded):
    segments = decoded.get("segments")
    if not isinstance(segments, list) or not segments or any(not isinstance(row, dict) for row in segments):
        raise ValueError("Expected a nonempty segment list in the LaunchPad readback")
    results = []
    ids = set()
    for row in segments:
        ident = row.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError("LaunchPad segment IDs must be present and unique")
        ids.add(ident)
        label = row.get("name") or ident
        if (row.get("type"), row.get("w"), row.get("h")) != (SEGMENT_TYPE, GRID_MM, GRID_MM):
            raise ValueError(f"LaunchPad segment {label!r} is outside the supported 240 mm S3-AS layout")
        x, y, master = row.get("x"), row.get("y"), row.get("master")
        if type(x) not in {int, float} or type(y) not in {int, float} or type(master) is not bool:
            raise ValueError(f"LaunchPad segment {label!r} needs numeric X/Y and a master flag")
        results.append(Segment(str(label), (Fraction(str(x)), Fraction(str(y))), master))
    return results


def native_segments(layout):
    if layout["gridCode"] != "0":
        raise ValueError(f"Expected the AS4 240 mm grid (GridSize 0), found {layout['gridCode']!r}")
    rows = layout["segments"]
    if not rows:
        raise ValueError("Expected one nonempty Segments group in the linked layout")
    results = []
    ids = set()
    for row in rows:
        ident = row["id"]
        if ident in ids or not re.fullmatch(r"Segment\[\d+\]", ident or ""):
            raise ValueError("Expected unique indexed Segment groups in the linked layout")
        ids.add(ident)
        label = row["name"] or ident
        if row["typeCode"] != "0":
            raise ValueError(f"AS4 segment {label!r} needs TypeGrid240 0 (S3-AS)")
        raw_x, raw_y = row["gridXY"]
        if not all(re.fullmatch(r"[+-]?[0-9]+", value or "") for value in (raw_x, raw_y)):
            raise ValueError(f"AS4 segment {label!r} needs integer X/Y grid positions")
        master = (row["master"] or "").casefold()
        if master not in {"true", "false", "1", "0"}:
            raise ValueError(f"AS4 segment {label!r} needs an explicit master flag")
        results.append(Segment(label, (int(raw_x) * GRID_MM, int(raw_y) * GRID_MM), master in {"true", "1"}))
    return results


def master_offsets(segments, label):
    masters = [item for item in segments if item.master]
    if len(masters) != 1:
        raise ValueError(f"{label} must declare exactly one master segment")
    master = masters[0]
    mx, my = master.xy_mm
    offsets = set()
    for item in segments:
        x, y = item.xy_mm
        dx, dy = x - mx, y - my
        if dx % GRID_MM or dy % GRID_MM:
            raise ValueError(f"{label} segment {item.label!r} is off the 240 mm grid relative to its master")
        cell = (int(dx // GRID_MM), int(dy // GRID_MM))
        if cell in offsets:
            raise ValueError(f"{label} contains multiple segments in the same grid cell")
        offsets.add(cell)
    position = [int(value) if value == int(value) else float(value) for value in master.xy_mm]
    spacing = sorted(hypot(x * GRID_MM, y * GRID_MM) for x, y in offsets)
    if not all(isfinite(value) for value in spacing):
        raise ValueError(f"{label} segment spacing exceeds the numeric range")
    return {"count": len(segments), "master": master.label, "masterPositionMm": position,
            "offsetsFromMasterMm": [[x * GRID_MM, y * GRID_MM] for x, y in sorted(offsets)],
            "distancesFromMasterMm": spacing}, offsets


def linked_layout(links, assembly):
    matches = [row for row in links if row["assembly"] == assembly]
    if len(matches) != 1 or len(matches[0]["layoutEvidence"]) != 1:
        raise ValueError("The selected manager must resolve through its assembly to exactly one layout")
    return matches[0]["layoutEvidence"][0]


def apply_axes(matrix, point):
    a, b, c, d = matrix
    x, y = point
    return a * x + b * y, c * x + d * y


def compare_layout(decoded, layout):
    saved, before = master_offsets(launchpad_segments(decoded), "LaunchPad")
    imported, after = master_offsets(native_segments(layout), "Automation Studio")
    matching = [label for label, matrix in AXES.items() if {apply_axes(matrix, point) for point in before} == after]
    issues = []
    if saved["count"] != imported["count"]:
        issues.append(("launchpad_segment_count_mismatch",
                       f"LaunchPad has {saved['count']} segments and the linked layout has {imported['count']}"))
    elif not matching:
        issues.append(("launchpad_master_layout_mismatch",
                       "The segment grid positions do not correspond relative to the declared master"))
    return {"state": "issues" if issues else "matched", "layoutId": layout["layoutId"], "path": layout["path"],
            "gridMm": GRID_MM, "segmentType": SEGMENT_TYPE, "launchPad": saved, "automationStudio": imported,
            "compatibleAxisMappings": matching, "userOriginRaw": layout["userOriginRaw"]}, issues
