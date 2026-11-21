"""Inventory the XML groups observed in the AS4 2.2 LaunchPad importer."""

from hashlib import sha256
from xml.etree import ElementTree as ET

from .project import ProjectXml, XML_LIMIT


GROUPS = {
    "Segments": None,
    "Stations": "NbOfStations",
    "Waypoints": "NbOfWaypoints",
    "Gates": "NbOfGates",
    "Branches": "NbOfBranches",
    "ExclusiveGroups": "NbOfExclusiveGroups",
}


def inventory_export(path, expected_sha256):
    with path.open("rb") as stream:
        data = stream.read(XML_LIMIT + 1)
    if len(data) > XML_LIMIT:
        raise ValueError(f"LaunchPad export shape inspection is limited to {XML_LIMIT} bytes")
    if sha256(data).hexdigest() != expected_sha256:
        raise ValueError("The LaunchPad export changed after fingerprinting")
    try:
        root = ET.fromstring(data, parser=ET.XMLParser(target=ProjectXml()))
    except (ET.ParseError, LookupError) as error:
        raise ValueError(f"Could not read the export as XML: {error}") from error

    groups = {}
    other = []
    for group in root.findall("Group"):
        identifier = group.get("ID")
        if identifier not in GROUPS:
            other.append(identifier)
            continue
        if identifier in groups:
            raise ValueError(f"Multiple top-level LaunchPad groups named {identifier}")
        entries = []
        for item in group.findall("Group"):
            key = item.get("ID")
            if key and key == GROUPS[identifier]:
                continue
            names = [prop.get("Value") for prop in item.findall("Property") if prop.get("ID") == "Name"]
            entries.append({"id": key, "name": names[0] if len(names) == 1 else None})
        groups[identifier] = entries
    if "Segments" not in groups:
        raise ValueError("Could not find the unqualified top-level Segments group read by the AS4 2.2 importer")
    return {
        "state": "read",
        "reference": "as4_mapp6d_2.2_launchpad_groups",
        "root": root.tag,
        "groups": [{"id": key, "count": len(entries), "entries": entries} for key, entries in groups.items()],
        "otherGroupIds": other,
    }
