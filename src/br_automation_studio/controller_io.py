"""Check saved cyclic 6D communication arrays against an exact SG4 module definition."""

from pathlib import Path
import re

from .assembly_io import assembly_io_values, match_declaration
from .global_variables import IDENTIFIER
from .project import children, package_files


WORD_COUNT = 200
MAP_LIMIT = 4 * 1024 * 1024
DIRECTIONS = {"Input": ("IN", "2007", "IW"), "Output": ("OUT", "2008", "QW")}
CHANNEL = re.compile(r"EPL_FB_(IN|OUT)_([0-9]{1,3})_I(2007|2008)_S([0-9A-F]{2})(Out)?")
MAP_BLOCK = re.compile(r"^[ \t]*VAR_CONFIG[ \t]*\n(.*?)^[ \t]*END_VAR[ \t]*;?[ \t]*$",
                       re.IGNORECASE | re.MULTILINE | re.DOTALL)
MAP_MARKER = re.compile(r"^[ \t]*(?:VAR_CONFIG|END_VAR)\b", re.IGNORECASE | re.MULTILINE)
COMMENTS_AND_STRINGS = re.compile(r'"(?:\$[^\n]|[^"$\n])*"|\(\*[\s\S]*?\*\)|//[^\n]*')
REFERENCE = re.compile(rf"::\s*({IDENTIFIER})\s*\.")
MAPPING = re.compile(
    rf'(?:Cyclic\#(?P<task>[1-9][0-9]?)\s*\.\s*)?::\s*(?P<variable>{IDENTIFIER})\s*\.\s*'
    r'Array\s*\[\s*(?P<index>[0-9]{1,3})\s*\]\s+AT\s+%(?P<access>[A-Za-z]{1,4})\s*\.\s*'
    r'"(?P<device>[^"\r\n]+)"\s*\.\s*(?P<channel>[A-Za-z0-9_]+)',
    re.IGNORECASE,
)


def catalog_words(definition):
    words = {role: {} for role in DIRECTIONS}
    directions = {fields[0]: role for role, fields in DIRECTIONS.items()}
    for section in children(definition, "Channels"):
        if section.get("Target") != "SG4":
            continue
        for channel in children(section, "Channel"):
            name = channel.get("ID") or ""
            if not name.startswith("EPL_FB_"):
                continue
            match = CHANNEL.fullmatch(name)
            if match is None:
                raise ValueError(f"Unsupported 6D SG4 channel identifier {name!r}")
            direction, decimal, obj, subindex, suffix = match.groups()
            role = directions[direction]
            index = int(decimal)
            fields = children(channel, "Parameter")
            values = {field.get("ID"): field.get("Value") for field in fields}
            address = values.get("Address") or ""
            offset = int(address, 16 if address.lower().startswith("0x") else 10)
            expected_suffix = "Out" if direction == "OUT" else None
            if (len(values) != len(fields) or values.get("Type") != "UINT" or values.get("Direction") != direction
                    or offset != index * 2 or obj != DIRECTIONS[role][1] or int(subindex, 16) != index + 1
                    or suffix != expected_suffix or index in words[role]):
                raise ValueError(f"Inconsistent or duplicate SG4 communication word {name!r}")
            words[role][index] = name
    for role, channels in words.items():
        if channels.keys() != set(range(WORD_COUNT)):
            raise ValueError(f"Expected SG4 {role} communication word indices 0 through {WORD_COUNT - 1}")
    return words


def map_body(raw):
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
    masked = COMMENTS_AND_STRINGS.sub(
        lambda token: token[0] if token[0].startswith('"') else re.sub(r"[^\n]", " ", token[0]), text)
    block = MAP_BLOCK.fullmatch(masked.strip())
    if block is None or MAP_MARKER.search(block[1]) or any(marker in masked for marker in ("\0", "(*", "*)")):
        raise ValueError("Expected one VAR_CONFIG block with complete IEC comments in the saved I/O mapping file")
    block_start = len(masked) - len(masked.lstrip())
    return block[1], masked.count("\n", 0, block_start) + 2


def check_map(raw, device, variables, words):
    body, first_line = map_body(raw)
    roles = {name.casefold(): role for role, name in variables.items()}
    seen = {role: set() for role in variables}
    invalid = []
    tasks = set()
    unqualified = 0
    line = first_line
    statements = body.split(";")
    for index, statement in enumerate(statements):
        row_line = line + statement[:len(statement) - len(statement.lstrip())].count("\n")
        line += statement.count("\n")
        reference = REFERENCE.search(statement)
        role = roles.get(reference[1].casefold()) if reference else None
        if role is None and not (f'"{device}"'.casefold() in statement.casefold()
                                 and "epl_fb_" in statement.casefold()):
            continue
        row = MAPPING.fullmatch(statement.strip())
        if row is None or index == len(statements) - 1:
            invalid.append({"line": row_line, "reason": "Unsupported or unterminated communication mapping"})
            continue
        slot = int(row["index"])
        if (role is None or row["device"] != device or row["access"].upper() != DIRECTIONS[role][2]
                or words[role].get(slot) != row["channel"]):
            invalid.append({"line": row_line, "reason": "Communication variable, device, access or channel does not match"})
            continue
        if slot in seen[role]:
            invalid.append({"line": row_line, "reason": f"Duplicate {role} array index {slot}"})
            continue
        seen[role].add(slot)
        if row["task"] is None:
            unqualified += 1
        else:
            tasks.add(int(row["task"]))
    groups = {role: {"matchedRows": len(slots), "missingIndices": sorted(words[role].keys() - slots)}
              for role, slots in seen.items()}
    return {"directions": groups, "invalidRows": invalid, "taskClasses": sorted(tasks), "unqualifiedRows": unqualified}


def review_task_timing(saved, hardware_path, cpu_name, cpu_type, task_class):
    hardware, _ = saved.xml(hardware_path, "Hardware")
    cpus = [module for module in children(hardware, "Module")
            if module.get("Name") == cpu_name and module.get("Type") == cpu_type]
    result = {"state": "unverified", "hardware": hardware_path.as_posix(),
              "durationMicroseconds": None, "toleranceMicroseconds": None}
    if len(cpus) != 1:
        saved.issue("warning", "controller_io_timing_unverified", hardware_path,
                    "Could not identify one top-level hardware module for the selected CPU")
        return result

    params = children(cpus[0], "Parameter")
    for field in ("Duration", "Tolerance"):
        key = f"Cyclic{task_class}{field}"
        values = [param.get("Value") for param in params if param.get("ID") == key]
        if len(values) == 1 and re.fullmatch(r"[0-9]{1,10}", values[0] or "") and int(values[0]) <= 0xFFFFFFFF:
            result[f"{field.lower()}Microseconds"] = int(values[0])
        else:
            saved.issue("warning", "controller_io_timing_unverified", hardware_path,
                        f"{key} has no single explicit unsigned 32-bit value on the selected CPU")
    if result["durationMicroseconds"] is not None and result["toleranceMicroseconds"] is not None:
        result["state"] = "saved"
    return result


def review_controller_map(saved, studio, report, hardware, assembly, expected_task_class=None):
    result = {"state": "unverified", "assembly": assembly, "controller": None, "map": None, "variables": {}}
    if expected_task_class is not None:
        result["expectedTaskClass"] = expected_task_class
    if hardware["state"] != "matched" or not report["selectedCpu"]:
        return result
    cpu = Path("Physical", report["selectedConfiguration"], report["selectedCpu"], "Cpu.pkg")
    if len(hardware["modules"]) != 1:
        saved.issue("error", "controller_io_selection_ambiguous", cpu,
                    "Select a configuration with one controller of the requested type for the communication map check")
        return result

    controller, = hardware["modules"]
    result["controller"] = controller["name"]
    catalog = Path(controller["catalog"])
    definition, _ = studio.xml(catalog, "Module")
    try:
        words = catalog_words(definition)
    except ValueError as error:
        studio.issue("error", "controller_io_catalog_unverified", catalog, str(error))
        return result

    try:
        io = assembly_io_values(assembly, report["assemblyLinks"])
        variables = {}
        for role, reference in io["references"].items():
            if reference["state"] != "declared":
                raise ValueError(f"The assembly needs a saved {role} communication variable reference")
            declaration = match_declaration(report["globalDeclarations"], reference)
            if declaration["state"] != "matched":
                raise ValueError(f"The assembly {role} reference must identify a registered Mc6DControllerFBIOType variable")
            variables[role] = declaration["name"]
        if len({name.casefold() for name in variables.values()}) != len(DIRECTIONS):
            raise ValueError("The Input and Output communication variables must be distinct")
        result["variables"] = variables
    except ValueError as error:
        saved.issue("error", "controller_io_reference_unverified", cpu, str(error))
        return result

    maps = package_files(saved, cpu, "Cpu", {"iom"})
    if len(maps) != 1:
        saved.issue("error", "controller_io_map_unverified", cpu, "Select a CPU with one registered .iom mapping file")
        return result
    mapping, = maps
    result["map"] = mapping.as_posix()
    raw = saved.read_bytes(mapping, MAP_LIMIT, label="I/O mapping file")
    if raw is None:
        return result
    try:
        result.update(check_map(raw, controller["name"], variables, words))
    except ValueError as error:
        saved.issue("error", "controller_io_map_unverified", mapping, str(error))
        return result

    missing = sum(len(group["missingIndices"]) for group in result["directions"].values())
    invalid = len(result["invalidRows"])
    if invalid or missing:
        result["state"] = "mismatched"
        saved.issue("error", "controller_io_map_mismatch", mapping,
                    f"The communication map has {invalid} invalid rows and {missing} unmapped array indices")
    elif (expected_task_class is not None
          and (result["taskClasses"] != [expected_task_class] or result["unqualifiedRows"])):
        result["state"] = "mismatched"
        saved.issue("error", "controller_io_task_class_mismatch", mapping,
                    f"All communication rows must explicitly use Cyclic#{expected_task_class} for this check")
    else:
        result["state"] = "matched"
    if expected_task_class is not None:
        result["taskTiming"] = review_task_timing(saved, Path(hardware["hardware"]), report["selectedCpu"],
                                                  report["cpu"].get("moduleId"), expected_task_class)
    return result
