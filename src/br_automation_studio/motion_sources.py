"""Locate selected AS4 motion libraries and CPU references."""

from .project import children, choices


def required_xml(source, path, root):
    node, _ = source.xml(path, root)
    if node is None:
        raise ValueError(f"Could not read selected motion metadata at {path.as_posix()}")
    return node


def library_folders(source, logical, root):
    libraries = choices(source, logical, root, "Package").get("libraries")
    if libraries is None:
        return {}
    index = libraries / "Package.pkg"
    return choices(source, index, required_xml(source, index, "Package"), "Library")


def selected_components(packages, name):
    matches = [row for package in packages if package["internalVersion"]
               for row in package["components"] if row["name"] == name and row["source"]]
    if len(matches) > 1:
        raise ValueError(f"Several selected catalog providers declare {name}")
    return matches[0] if matches else {}


def cpu_sources(software, group, object_type, name):
    matches = [item.get("Source") or "" for parent in children(software, group) for item in children(parent, object_type)
               if (item.get("Name") or "").casefold() == name.casefold()]
    if len(matches) > 1:
        raise ValueError(f"The selected CPU declares several sources for {name}")
    return matches


def verify_library_reference(software, name, folder):
    references = cpu_sources(software, "Libraries", "LibraryObject", name)
    if references:
        expected = ".".join((*folder.relative_to("Logical").parts, "lby")).casefold() if folder else None
        if references[0].casefold() != expected:
            raise ValueError(f"The selected CPU reference for {name} differs from its registered library")
    return bool(references)
