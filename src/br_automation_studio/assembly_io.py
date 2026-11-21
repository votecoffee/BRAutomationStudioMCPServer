"""Trace saved assembly I/O to its IEC declarations and compiled configuration."""

from pathlib import Path
import re

from .global_variables import IDENTIFIER
from .inspection import inspect_project
from .links import READ_AXES

IO_TYPE = "Mc6DControllerFBIOType"
AXIS_FLAGS = {"true": True, "false": False}


def normalize(field, *, axis):
    record = dict(field, value=None)
    raw = field["raw"]
    if field["state"] != "declared":
        return record
    if isinstance(raw, str):
        if axis:
            record["value"] = AXIS_FLAGS.get(raw.casefold())
        elif re.fullmatch(IDENTIFIER, raw):
            record["value"] = raw.casefold()
    if record["value"] is None:
        record["state"] = "unverified"
    return record


def comparison(saved, compiled):
    if compiled is None:
        return "not_checked"
    if "unverified" in (saved["state"], compiled["state"]):
        return "unverified"
    if saved["state"] == "absent":
        return "materialized" if compiled["state"] == "declared" else "unverified"
    return "matched" if saved["value"] == compiled["value"] else "mismatch"


def match_declaration(global_variables, field):
    candidates = [row for row in global_variables["declarations"] if row["name"].casefold() == field["value"]]
    if len(candidates) != 1 or candidates[0]["type"] is None:
        return {"state": "unverified", "path": global_variables["path"]}
    declaration, = candidates
    state = "matched" if declaration["type"].casefold() == IO_TYPE.casefold() else "wrong_type"
    return dict(declaration, state=state, path=global_variables["path"])


def assembly_io_values(assembly, links):
    raw = next((row["ioFields"] for row in links if row["assembly"] == assembly), None)
    if raw is None:
        raise ValueError(f"Cannot read I/O configuration for the reviewed assembly {assembly!r}")
    return {"assembly": assembly, "path": raw["path"],
            "references": {key: normalize(field, axis=False) for key, field in raw["references"].items()},
            "readAxes": {key: normalize(field, axis=True) for key, field in raw["readAxes"].items()}}


def pinned_assembly_io(assembly, links, variables, axes):
    if not isinstance(axes, list) or not axes or any(axis not in READ_AXES for axis in axes) or len(set(axes)) != len(axes):
        raise ValueError("Select distinct assembly read axes from X, Y, Z, Rx, Ry and Rz")
    saved = assembly_io_values(assembly, links)
    for axis in axes:
        if saved["readAxes"][f"{axis}axis"]["value"] is not True:
            raise ValueError(f"{axis}axis needs an explicit TRUE in {saved['path']}")
    for key, field in saved["readAxes"].items():
        if field["state"] == "unverified":
            raise ValueError(f"{key} needs a supported Boolean in {saved['path']}")
    declarations = {}
    for key, field in saved["references"].items():
        declaration = match_declaration(variables, field)
        if declaration["state"] != "matched":
            raise ValueError(f"{key} needs a reference to one {IO_TYPE} declaration in the registered root Global.var")
        declarations[key] = declaration
    return {"saved": saved, "requiredReadAxes": axes, "declarations": declarations}


def assembly_io_review(project, configuration, cpu, assembly, job_directory=None):
    if job_directory is None:
        report = inspect_project(project, configuration, cpu, include_io=True)
        phase = report
        saved_links = report["assemblyLinks"]
    else:
        from .ruc_review import review_ruc

        report = review_ruc(project, job_directory, include_io=True)
        phase = report["rucReview"]
        saved_links = phase.get("savedAssemblyLinks", [])

    variables = phase.pop("globalDeclarations", None) or {"path": None, "declarations": []}
    check = report["ioReview"] = {"assembly": assembly, "state": "not_checked", "references": {}, "readAxes": {}}
    diagnostics = []

    def issue(code, path, message, severity="error"):
        diagnostics.append({"code": code, "path": Path(path or project).as_posix(), "message": message,
                            "severity": severity, "scope": "assembly_io"})

    if not report["selectedConfiguration"] or not report["selectedCpu"]:
        return report
    if job_directory is not None:
        if not phase["sourceMatched"] or phase["archive"] is None:
            return report
        for requested, key in ((configuration, "selectedConfiguration"), (cpu, "selectedCpu")):
            if requested is not None and requested.casefold() != report[key].casefold():
                issue("assembly_io_selection_mismatch", project, "Requested configuration or CPU differs from the retained build")
                report["diagnostics"].extend(diagnostics)
                return report

    saved = next((row["ioFields"] for row in saved_links if row["assembly"] == assembly), None)
    selected = next((row["ioFields"] for row in phase["assemblyLinks"] if row["assembly"] == assembly), None)
    if saved is None:
        issue("assembly_io_unavailable", project, f"Could not read saved assembly {assembly!r} in the selected CPU")
        check["state"] = "issues"
    elif selected is None:
        issue("assembly_io_unverified", project, f"Could not read compiled assembly {assembly!r}", "warning")
        check["state"] = "unverified"
    else:
        check["savedPath"] = saved["path"]
        check["compiledPath"] = selected["path"] if job_directory is not None else None
        for group in ("references", "readAxes"):
            axis = group == "readAxes"
            for key, field in saved[group].items():
                before = normalize(field, axis=axis)
                after = normalize(selected[group][key], axis=axis) if job_directory is not None else None
                outcome = comparison(before, after)
                row = {"saved": before, "compiled": after, "comparison": outcome}
                if outcome == "mismatch":
                    issue("ruc_io_mismatch", selected["path"], f"Compiled {key} differs from its explicit saved value")
                elif outcome == "unverified" or before["state"] == "unverified":
                    issue("assembly_io_unverified", selected["path"], f"Cannot verify the declaration for {key}", "warning")
                if not axis:
                    declaration = match_declaration(variables, after if after is not None else before)
                    row["declaration"] = declaration
                    if declaration["state"] == "wrong_type":
                        issue("assembly_io_wrong_type", declaration["path"],
                              f"{key} reference {declaration['name']!r} has type {declaration['type']}, expected {IO_TYPE}")
                    elif declaration["state"] == "unverified":
                        issue("assembly_io_declaration_unverified", declaration["path"],
                              f"Cannot establish the {key} reference in the registered root Global.var", "warning")
                check[group][key] = row
        check["state"] = "issues" if any(row["severity"] == "error" for row in diagnostics) else "unverified" if diagnostics else "read"
    report["diagnostics"].extend(diagnostics)
    if not report["diagnostics"]:
        report["evidence"] = "assembly_io_compiled" if job_directory is not None else "assembly_io_saved"
    return report
