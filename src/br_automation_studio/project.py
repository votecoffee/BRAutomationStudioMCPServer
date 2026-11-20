
"""Read declared Automation Studio project files and retain their identities."""

from hashlib import file_digest, sha256
import json
from pathlib import Path
from xml.etree import ElementTree as ET

MOTION_TYPES = {
    "assembly6d", "layout6d", "trafficmanager",
    "trafficobjectlist", "trafficmappinglist", "trafficrulelist",
}
XML_LIMIT = 16 * 1024 * 1024


def tag(node):
    return node.tag.rpartition("}")[2]


def children(node, name):
    return [child for child in node if tag(child) == name]


def objects(node, kind):
    for group in children(node, "Objects"):
        yield from (item for item in children(group, "Object") if item.get("Type") == kind)


class ProjectXml(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.studio = {}

    def doctype(self, name, public_id, system_id):
        raise ValueError("DOCTYPE declarations are outside the supported XML format")

    def pi(self, target, data):
        if target == "AutomationStudio":
            try:
                settings = ET.fromstring(f"<Studio {data} />")
            except ET.ParseError:
                # Some AS library descriptors use an unquoted version in the PI.
                # The PI remains valid XML and is independent of the Library element.
                return
            self.studio = dict(settings.attrib)


class SavedFiles:
    def __init__(self, root):
        self.root = root.resolve()
        self.sources = {}
        self.diagnostics = []
        self.parsed = {}

    def issue(self, severity, code, path, message):
        self.diagnostics.append({
            "severity": severity, "code": code, "path": path.as_posix(), "message": message,
        })

    def member(self, manifest, name):
        # AS package entries refer to direct children. Apply Windows name rules on every host.
        value = (name or "").strip()
        reserved = {"CON", "PRN", "AUX", "NUL"}
        reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³")
        if (not value or value.endswith(".") or value.split(".")[0].upper() in reserved
                or any(ch in value for ch in '\\/:*?"<>|') or any(ord(ch) < 32 for ch in value)):
            self.issue("error", "invalid_member", manifest, f"Invalid package member {value!r}")
            return None
        return manifest.parent / value

    def resolve(self, path):
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            self.issue("error", "outside_project", path, "Path resolves outside the selected source root")
            return None
        if resolved.suffix.casefold() != path.suffix.casefold():
            self.issue("error", "unsupported_file", path, "Resolved path has a different file extension")
            return None
        return resolved

    def digest(self, path):
        try:
            source = self.resolve(path)
            if source is None:
                return None
            with source.open("rb") as stream:
                fingerprint = file_digest(stream, "sha256").hexdigest()
        except (OSError, RuntimeError) as error:
            self.issue("error", "unreadable_file", path, f"Could not hash metadata file ({type(error).__name__})")
            return None
        self.sources[path.as_posix()] = fingerprint
        return fingerprint

    def read_bytes(self, path, limit, *, optional=False, label="Project file"):
        try:
            source = self.resolve(path)
            if source is None:
                return None
            with source.open("rb") as stream:
                data = stream.read(limit + 1)
        except FileNotFoundError:
            if not optional:
                self.issue("error", "missing_file", path, "Expected file was not found")
            return None
        except (OSError, RuntimeError) as error:
            self.issue("error", "unreadable_file", path, f"Could not read project file ({type(error).__name__})")
            return None
        if len(data) > limit:
            self.issue("error", "file_too_large", path, f"{label} exceeds {limit} bytes")
            return None
        self.sources[path.as_posix()] = sha256(data).hexdigest()
        return data

    def xml(self, path, expected, *, optional=False):
        if path in self.parsed:
            return self.parsed[path]

        self.parsed[path] = (None, {})
        data = self.read_bytes(path, XML_LIMIT, optional=optional, label="XML input")
        if data is None:
            return None, {}
        parser = ProjectXml()
        try:
            node = ET.fromstring(data, parser=ET.XMLParser(target=parser))
        except (ET.ParseError, LookupError, ValueError) as error:
            self.issue("error", "invalid_xml", path, str(error))
            return None, {}
        if tag(node) != expected:
            self.issue("error", "unexpected_root", path, f"Expected root element {expected}, found {tag(node)}")
            return None, {}
        self.parsed[path] = node, parser.studio
        return self.parsed[path]

    def snapshot(self):
        files = [{"path": path, "sha256": digest} for path, digest in sorted(self.sources.items())]
        fingerprint = json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return {"algorithm": "sha256", "sha256": sha256(fingerprint.encode("ascii")).hexdigest(), "files": files}


def choices(source, manifest, node, kind):
    members = [source.member(manifest, item.text) for item in objects(node, kind)]
    result = {}
    for member in members:
        if member is not None:
            key = member.name.casefold()
            if key in result:
                source.issue("error", "duplicate_choice", manifest, f"Duplicate {kind} {member.name!r}")
                continue
            result[key] = member
    return result


def select(source, manifest, members, name, kind):
    if name is not None:
        choice = members.get(name.casefold())
        if choice is None:
            source.issue("error", "unknown_selection", manifest, f"Unknown {kind} {name!r}")
        return choice
    if len(members) == 1:
        return next(iter(members.values()))
    if len(members) > 1:
        source.issue("warning", "selection_required", manifest, f"Select a {kind} to check its links")
    else:
        source.issue("warning", "empty_selection", manifest, f"No {kind} is declared")
    return None


def package_files(source, start, root_name, extensions):
    pending = [(start, root_name)]
    visited = set()
    files = set()
    while pending:
        path, expected = pending.pop()
        try:
            key = (source.root / path).resolve()
        except (OSError, RuntimeError):
            key = path
        if key in visited:
            source.issue("warning", "repeated_package", path, "Package was already visited")
            continue
        visited.add(key)
        node, _ = source.xml(path, expected)
        if node is None:
            continue
        for item in objects(node, "Package"):
            member = source.member(path, item.text)
            if member is not None:
                pending.append((member / "Package.pkg", "Package"))
        for item in objects(node, "File"):
            if Path((item.text or "").strip()).suffix.lower().lstrip(".") not in extensions:
                continue
            member = source.member(path, item.text)
            if member is not None:
                files.add(member)
    return sorted(files)
