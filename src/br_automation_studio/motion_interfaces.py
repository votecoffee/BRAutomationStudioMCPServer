"""Read public IEC function-block pins and STRUCT fields from the selected mapp 6D library files."""

from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import re

from .preflight import build_preflight
from .project import SavedFiles, XML_LIMIT


IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
SYMBOL = re.compile(IDENTIFIER)
FIELD_PATH = re.compile(rf"{IDENTIFIER}(?:\.{IDENTIFIER}){{2,7}}")
LEXICAL = re.compile(r"\(\*|\*\)|//|['\"]")
COMMENT_MARKER = re.compile(r"\(\*|\*\)")
STRING = re.compile(r"""'(?:\$[^\r\n]|[^'$\r\n])*'|"(?:\$[^\r\n]|[^"$\r\n])*" """, re.VERBOSE)
BLOCK = re.compile(rf"^[ \t]*(FUNCTION_BLOCK|END_FUNCTION_BLOCK)\b(?:[ \t]+({IDENTIFIER}))?([^\r\n]*)", re.I | re.M)
PUBLIC_START = re.compile(r"^[ \t]*(VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT)\b([^\r\n]*)\r?$", re.I | re.M)
TYPE_MARKER = re.compile(
    rf"^[ \t]*(?:(TYPE|END_TYPE|END_STRUCT)\b(?:[ \t]*;)?|({IDENTIFIER})[ \t]*:\s*STRUCT\b)[ \t]*\r?$",
    re.I | re.M,
)
END_VAR = re.compile(r"^[ \t]*END_VAR\b[ \t]*\r?$", re.I | re.M)
DECLARATION = re.compile(rf"\s*({IDENTIFIER}(?:\s*,\s*{IDENTIFIER})*)\s*:\s*(\S.*?)\s*$", re.S)
STATEMENT = re.compile(r"[^;]*;")
VALUE_TYPE = re.compile(
    rf"(?:REFERENCE TO |ARRAY\s*\[[\w\s.,+-]+\]\s*OF )*"
    rf"{IDENTIFIER}(?:(?:::|\.){IDENTIFIER})*(?:\s*\[\s*\d+\s*\])?", re.I
)
DIRECTIONS = {"VAR_INPUT": "input", "VAR_OUTPUT": "output", "VAR_IN_OUT": "in_out"}


def blank_span(text):
    return "".join(char if char in "\r\n" else " " for char in text)


def comment_end(text, offset):
    depth = 1
    for marker in COMMENT_MARKER.finditer(text, offset):
        depth += 1 if marker[0] == "(*" else -1
        if depth == 0:
            return marker.end()
    raise ValueError("Unclosed IEC block comment")


def lexical_view(text):
    result = []
    cursor = 0
    while token := LEXICAL.search(text, cursor):
        start = token.start()
        result.append(text[cursor:start])
        marker = token[0]
        if marker == "(*":
            cursor = comment_end(text, token.end())
        elif marker == "//":
            newline = text.find("\n", token.end())
            cursor = len(text) if newline < 0 else newline
        elif literal := STRING.match(text, start):
            cursor = literal.end()
        else:
            raise ValueError(f"Unclosed IEC string or unexpected comment terminator at line {text.count(chr(10), 0, start) + 1}")
        result.append(blank_span(text[start:cursor]))
    result.append(text[cursor:])
    return "".join(result)


def read_blocks(text):
    blocks = defaultdict(list)
    active = None
    for marker in BLOCK.finditer(text):
        keyword, name, tail = marker.groups()
        if keyword.upper() == "FUNCTION_BLOCK":
            if active is not None or name is None:
                raise ValueError("Missing function-block name or END_FUNCTION_BLOCK")
            if tail.strip():
                raise ValueError(f"Unsupported function-block header for {name}")
            active = marker
        else:
            if active is None:
                raise ValueError("END_FUNCTION_BLOCK without a declaration")
            start = active.start(2)
            blocks[active[2].casefold()].append((active[2], text.count("\n", 0, start) + 1,
                                                text[active.end():marker.start()]))
            active = None
    if active is not None:
        raise ValueError(f"Missing END_FUNCTION_BLOCK for {active[2]}")
    return blocks


def declared_members(fields, first_line, section):
    members = []
    cursor = 0
    for member in STATEMENT.finditer(fields):
        content = member[0][:-1]
        offset = member.start() + len(content) - len(content.lstrip())
        line = first_line + fields.count("\n", 0, offset)
        match = DECLARATION.fullmatch(content.split(":=", 1)[0])
        if match is None:
            raise ValueError(f"Unsupported {section} declaration at line {line}")
        names, type_text = match.groups()
        value_type = " ".join(type_text.split())
        if VALUE_TYPE.fullmatch(value_type) is None:
            raise ValueError(f"Unsupported {section} type at line {line}")
        for name in names.split(","):
            members.append({"name": name.strip(), "type": value_type, "line": line})
        cursor = member.end()
    if fields[cursor:].strip():
        raise ValueError(f"Unterminated {section} declaration")
    return members


def public_pins(body, block_line):
    pins, seen = [], set()
    previous_end = 0
    for group in PUBLIC_START.finditer(body):
        if group[2].strip():
            raise ValueError(f"Unsupported qualifier on {group[1]} section")
        end = END_VAR.search(body, group.end())
        direction = DIRECTIONS[group[1].upper()]
        if group.start() < previous_end or end is None:
            raise ValueError(f"Unclosed or overlapping {group[1]} section")
        previous_end = end.end()
        fields = body[group.end():end.start()]
        first_line = block_line + body.count("\n", 0, group.end())
        for pin in declared_members(fields, first_line, group[1]):
            key = pin["name"].casefold()
            if key in seen:
                raise ValueError(f"Duplicate public pin {pin['name']!r} at line {pin['line']}")
            seen.add(key)
            pins.append({**pin, "direction": direction})
    if not pins:
        raise ValueError("No supported public input, output or in-out pins found")
    return pins


def read_structures(text):
    structures = defaultdict(list)
    in_types = False
    active = None
    for marker in TYPE_MARKER.finditer(text):
        token, name = marker.groups()
        word = token.upper() if token else None
        if word == "TYPE" and not in_types:
            in_types = True
        elif word == "END_TYPE" and in_types and active is None:
            in_types = False
        elif name and in_types and active is None:
            active = marker
        elif word == "END_STRUCT" and active is not None:
            owner = active[2]
            line = text.count("\n", 0, active.end()) + 1
            structures[owner.casefold()].append((owner, line, text[active.end():marker.start()]))
            active = None
        else:
            raise ValueError("Unbalanced IEC TYPE or STRUCT declaration")
    if in_types or active is not None:
        raise ValueError("Unclosed IEC TYPE or STRUCT declaration")
    return structures


def indexed_libraries(project, report, result, suffix, parse):
    readers = {"project": SavedFiles(project.parent),
               "catalog": SavedFiles(Path(report["preflight"]["studioRoot"]))}
    indexed = defaultdict(list)
    for library in report["motionDeclarations"]["libraries"]:
        name = library["name"]
        files = (row for row in library["files"] if
                 (row["name"] == name.casefold() + ".fun" if suffix == ".fun" else row["name"].endswith(suffix)))
        for file in files:
            origin = "project" if file["project"] is not None else "catalog"
            evidence = file[origin]
            relative = Path(evidence["path"])
            try:
                raw = readers[origin].read_bytes(relative, XML_LIMIT, label="IEC declarations")
                if raw is None or sha256(raw).hexdigest() != evidence["sha256"]:
                    raise ValueError("Declaration source changed or could not be read after preflight")
                declarations = parse(lexical_view(raw.decode("utf-8-sig")))
            except (OSError, RuntimeError, ValueError) as error:
                report["diagnostics"].append({"severity": "error", "scope": "motion_interface", "path": relative.as_posix(),
                                              "code": "motion_function_file_unverified" if suffix == ".fun"
                                              else "motion_type_file_unverified", "message": str(error)})
                result["state"] = "unverified"
                continue
            descriptor = {"library": name, "version": library["version"], "origin": origin, **evidence}
            result["files"].append(descriptor)
            for symbol, entries in declarations.items():
                indexed[symbol].extend((descriptor, entry) for entry in entries)
    return indexed


def trace_field(path, blocks, structures, row):
    block_name, pin, *fields = path.split(".")
    block = blocks.get(block_name.casefold())
    if block is None:
        raise ValueError(f"Could not verify function block {block_name} for {path}")
    pins = [item for item in block["pins"] if item["name"].casefold() == pin.casefold()]
    if len(pins) != 1:
        row.update(state="missing", source=block["source"], owner=block["name"], line=block["line"])
        raise ValueError(f"{block['name']} does not declare public pin {pin}")
    current, = pins
    row["trace"] = [{**current, "source": block["source"], "owner": block["name"]}]
    for field in fields:
        owners = structures.get(current["type"].casefold(), [])
        if len(owners) != 1:
            raise ValueError(f"Expected one direct STRUCT declaration for {current['type']} in {path}, found {len(owners)}")
        source, (owner, line, fields_text) = owners[0]
        row.update(source=source, owner=owner, line=line)
        matches = [item for item in declared_members(fields_text, line, owner)
                   if item["name"].casefold() == field.casefold()]
        if not matches:
            row["state"] = "missing"
            raise ValueError(f"{owner} does not declare field {field}")
        if len(matches) != 1:
            raise ValueError(f"{owner} has several declarations for field {field}")
        current, = matches
        row["trace"].append({**current, "source": source, "owner": owner})
    row.update(state="verified", type=current["type"], line=current["line"])


def review_fields(project, report, result, requested):
    result["fields"] = [{"path": path, "state": "unverified", "trace": []} for path in requested]
    structures = indexed_libraries(project, report, result, ".typ", read_structures)
    if result["state"] == "unverified":
        return
    blocks = {block["name"].casefold(): block for block in result["blocks"] if block["state"] == "verified"}
    for row in result["fields"]:
        try:
            trace_field(row["path"], blocks, structures, row)
        except ValueError as error:
            report["diagnostics"].append({"severity": "error", "scope": "motion_interface",
                                          "path": row.get("source", {}).get("path", project.name),
                                          "code": "motion_field_missing" if row["state"] == "missing"
                                          else "motion_field_unverified", "message": f"{row['path']}: {error}"})
            result["state"] = "issues"


def review_motion_interfaces(project, studio, runtime, configuration, cpu, requested, field_paths=()):
    report = build_preflight(project, studio, runtime, configuration, cpu, verify_motion_declarations=True)
    declarations = report["motionDeclarations"]
    result = report["motionInterfaceReview"] = {
        "state": "unverified", "mapp6DSelector": declarations.get("mapp6DSelector"),
        "mapp6DBuild": declarations.get("mapp6DBuild"), "files": [], "blocks": [],
    }
    if declarations["state"] != "verified":
        return report

    result["state"] = "verified"
    index = indexed_libraries(project, report, result, ".fun", read_blocks)
    if result["state"] == "unverified":
        return report
    for symbol in requested:
        rows = index.get(symbol.casefold(), [])
        row = {"name": symbol, "state": "unverified"}
        report_path = project.name
        try:
            if len(rows) != 1:
                raise ValueError(f"Expected one selected function block {symbol}, found {len(rows)}")
            source, (name, line, body) = rows[0]
            report_path = source["path"]
            row.update(name=name, source=source, line=line, pins=public_pins(body, line), state="verified")
        except ValueError as error:
            report["diagnostics"].append({"severity": "error", "scope": "motion_interface", "path": report_path,
                                          "code": "motion_function_block_unverified", "message": str(error)})
            result["state"] = "issues"
        result["blocks"].append(row)
    if field_paths:
        review_fields(project, report, result, field_paths)
    return report
