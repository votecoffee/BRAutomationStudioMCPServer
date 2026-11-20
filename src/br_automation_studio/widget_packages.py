"""Inventory entries next to the declared files in saved mapp View widget libraries."""

from pathlib import Path
import stat

from .project import SavedFiles, children, choices
from .widget_build_review import add_build_evidence


MEMBER_TYPES = {"File": "file", "Package": "directory"}
NATIVE_WARNINGS = {"file": 9232, "directory": 9233}


def registered_widgets(source):
    path = Path("Logical/Package.pkg")
    for name in ("mappview", "widgets"):
        node, _ = source.xml(path, "Package")
        if node is None:
            return None
        directory = choices(source, path, node, "Package").get(name)
        if directory is None:
            return None
        path = directory / "Package.pkg"
    return path


def directory_entries(source, manifest):
    path = source.resolve(manifest)
    if path is None:
        return None
    if path != source.root / manifest:
        source.issue("warning", "widget_library_inventory_unverified", manifest,
                     "Widget library manifest resolves to a different path")
        return None
    entries = {}
    try:
        for member in sorted(path.parent.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
            info = member.lstat()
            if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                kind = "unsupported"
            elif stat.S_ISDIR(info.st_mode):
                kind = "directory"
            elif stat.S_ISREG(info.st_mode):
                kind = "file"
            else:
                kind = "unsupported"
            key = member.name.casefold()
            if key == manifest.name.casefold():
                continue
            if key in entries:
                raise ValueError(f"Several directory entries match {member.name!r} on Windows")
            entries[key] = {"name": member.name, "kind": kind, "path": manifest.with_name(member.name).as_posix()}
    except (OSError, ValueError) as error:
        source.issue("warning", "widget_library_inventory_unverified", manifest, str(error))
        return None
    return entries


def declared_entries(source, manifest, node):
    declarations = {}
    for group in children(node, "Objects"):
        for item in children(group, "Object"):
            kind = MEMBER_TYPES.get(item.get("Type"))
            if kind is None:
                source.issue("warning", "widget_library_member_type_unverified", manifest,
                             f"Cannot classify saved widget library object type {item.get('Type')!r}")
                return None
            path = source.member(manifest, item.text)
            if path is None:
                return None
            key = path.name.casefold()
            if key == manifest.name.casefold() or key in declarations:
                source.issue("error", "widget_library_member_ambiguous", manifest,
                             f"Widget library has an ambiguous declaration for {path.name!r}")
                return None
            declarations[key] = {"name": path.name, "kind": kind, "path": path.as_posix()}
    return declarations


def inspect_library(source, manifest):
    result = {"manifest": manifest.as_posix(), "state": "unverified", "declared": [], "extra": [], "missing": []}
    node, _ = source.xml(manifest, "Package")
    if node is None:
        return result
    if "widgetlibrary" not in {(node.get(key) or "").casefold() for key in ("PackageType", "SubType")}:
        source.issue("warning", "widget_library_type_unverified", manifest,
                     "Expected a child package declared as WidgetLibrary")
        return result
    declarations = declared_entries(source, manifest, node)
    observed = directory_entries(source, manifest)
    if declarations is None or observed is None:
        return result

    for key, declared in sorted(declarations.items()):
        actual = observed.get(key)
        row = {**declared, "observedKind": actual["kind"] if actual is not None else None}
        result["declared"].append(row)
        if row["observedKind"] != declared["kind"]:
            result["missing"].append(row)
            source.issue("warning", "widget_library_member_missing", Path(row["path"]),
                         f"Expected a declared widget library {declared['kind']}")
    for key, entry in observed.items():
        if key in declarations:
            continue
        code = NATIVE_WARNINGS.get(entry["kind"])
        result["extra"].append({**entry, "builderWarning": code})
        source.issue("warning", "widget_library_extra_member", Path(entry["path"]),
                     f"Saved widget library has an undeclared {entry['kind']} entry")
    result["state"] = "differences" if result["extra"] or result["missing"] else "matched"
    return result


def widget_package_review(project, job_directory=None):
    source = SavedFiles(project.parent)
    node, _ = source.xml(Path(project.name), "Project")
    review = {"state": "unverified", "widgetsManifest": None, "libraries": []}
    if node is not None:
        widgets_manifest = registered_widgets(source)
        if widgets_manifest is None:
            if not source.diagnostics:
                review["state"] = "not_registered"
        else:
            review["widgetsManifest"] = widgets_manifest.as_posix()
            widgets, _ = source.xml(widgets_manifest, "Package")
            if widgets is not None:
                libraries = choices(source, widgets_manifest, widgets, "Package")
                review["libraries"] = [inspect_library(source, directory / "Package.pkg")
                                       for directory in sorted(libraries.values())]
                states = {library["state"] for library in review["libraries"]}
                if not source.diagnostics and not states:
                    review["state"] = "not_registered"
                elif "unverified" in states or any(item["severity"] == "error" for item in source.diagnostics):
                    review["state"] = "unverified"
                else:
                    review["state"] = "differences" if "differences" in states else "matched"
    report = {"evidence": "saved_widget_package_inventory", "project": project.name, "widgetPackageReview": review,
              "snapshot": source.snapshot(), "diagnostics": source.diagnostics}
    if job_directory is not None:
        add_build_evidence(project, job_directory, report)
    return report
