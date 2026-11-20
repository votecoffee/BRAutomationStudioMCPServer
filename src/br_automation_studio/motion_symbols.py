
"""Verify AS4 IEC globals generated for configured ACOPOS 6D objects."""

from pathlib import Path
import re

from .global_variables import GLOBAL_LIMIT
from .motion_interfaces import IDENTIFIER, lexical_view
from .project import SavedFiles


ASSEMBLY_FILE = "Mc6DElements.var"
TRAFFIC_FILE = "TrafficElements.var"
TRAFFIC_TYPES = {"Station": "MpTrafficStationType", "Waypoint": "MpTrafficWaypointType"}
NAME = re.compile(IDENTIFIER)
VAR_BLOCK = re.compile(r"\s*VAR(?:_GLOBAL)?[ \t]*\n(.*?)^[ \t]*END_VAR[ \t]*;?\s*", re.DOTALL | re.MULTILINE | re.I)
VAR_DECLARATION = re.compile(rf"\s*({IDENTIFIER})\s*:\s*(?:\{{{IDENTIFIER}}}\s*)*({IDENTIFIER})\s*;\s*")


def plan_motion_symbols(report):
    assembly_names = {row["assembly"] for row in report["assemblyLinks"]}
    if not assembly_names:
        raise ValueError("Select a configuration with a registered 6D assembly")
    expected = {ASSEMBLY_FILE: [{"name": row["assembly"], "type": "Mc6DAssemblyType"}
                                for row in report["assemblyLinks"]]}
    for manager in report["trafficManagers"]:
        if manager["assembly"] not in assembly_names or not manager["objectLists"]:
            raise ValueError(f"Could not resolve the assembly or object list for {manager['manager']!r}")
        traffic = expected.setdefault(TRAFFIC_FILE, [])
        traffic.append({"name": manager["manager"], "type": "MpTrafficManagerType"})
        for item in manager["objects"]:
            item_type = TRAFFIC_TYPES.get(item["type"])
            if item_type is None:
                raise ValueError(f"Unsupported generated type for {item['type']!r} object {item['name']!r}")
            traffic.append({"name": item["name"], "type": item_type})

    seen = set()
    for rows in expected.values():
        for row in rows:
            name = row["name"]
            if not isinstance(name, str) or not NAME.fullmatch(name) or name.casefold() in seen:
                raise ValueError(f"Expected a unique IEC name for generated symbol {name!r}")
            seen.add(name.casefold())
    return expected


def declarations(data, filename):
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = lexical_view(data.decode(encoding).replace("\r\n", "\n").replace("\r", "\n"))
    block = VAR_BLOCK.fullmatch(text)
    if block is None:
        raise ValueError(f"{filename} does not contain a supported generated VAR block")
    start = text.count("\n", 0, block.start(1)) + 1
    result = {}
    for line, content in enumerate(block[1].splitlines(), start):
        if not content.strip():
            continue
        match = VAR_DECLARATION.fullmatch(content)
        if match is None:
            raise ValueError(f"Unsupported generated declaration in {filename} at line {line}")
        name, type_name = match.groups()
        if name.casefold() in result:
            raise ValueError(f"Duplicate generated symbol {name!r} in {filename} at line {line}")
        result[name.casefold()] = {"name": name, "type": type_name, "line": line}
    return result


def read_motion_symbols(root, configuration, expected):
    files = SavedFiles(root)
    folder = files.member(Path("temporary/Includes/AS_TempDecl/_"), configuration)
    if folder is None or folder.name != configuration:
        raise ValueError("Invalid configuration for generated motion declarations")
    if not isinstance(expected, dict) or ASSEMBLY_FILE not in expected or expected.keys() - {ASSEMBLY_FILE, TRAFFIC_FILE}:
        raise ValueError("Invalid generated motion symbol plan")
    result = []
    seen = set()
    for filename, rows in expected.items():
        if not isinstance(rows, list) or not rows or any(
            not isinstance(row, dict) or any(not isinstance(row.get(key), str) for key in ("name", "type"))
            for row in rows
        ):
            raise ValueError(f"Invalid generated declaration plan for {filename}")
        relative = folder / "GlobalComponents" / filename
        data = files.read_bytes(relative, GLOBAL_LIMIT, label="Generated IEC declaration file")
        if data is None:
            raise ValueError(f"Could not read {relative.as_posix()}: {files.diagnostics[-1]['message']}")
        actual = declarations(data, filename)
        duplicates = seen.intersection(actual)
        if duplicates:
            raise ValueError(f"Duplicate generated symbols across files: {', '.join(sorted(duplicates))}")
        seen.update(actual)
        matched = []
        for row in rows:
            symbol = actual.get(row["name"].casefold())
            if symbol is None or symbol["type"].casefold() != row["type"].casefold():
                raise ValueError(f"Expected {row['name']} : {row['type']} in {filename}")
            matched.append(symbol)
        path = relative.as_posix()
        result.append({"path": path, "sha256": files.sources[path], "bytes": len(data), "declarations": matched})
    return result
