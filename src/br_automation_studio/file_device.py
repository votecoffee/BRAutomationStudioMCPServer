"""Read the AS4 CPU declaration and compiled system device tree for 6DData."""

from hashlib import sha256
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from .project import ProjectXml, XML_LIMIT, children, tag

DEVICE_NAME = "6DData"
USER_PARTITION = "USER_PATH"
COMPILED_USER_PARTITION = f"/DEVICE={USER_PARTITION}"
SYSTEM_CONFIG = "FilesToTransfer/Data0/$$sysconf.br"
SLOT_NAME = re.compile(r"FileDeviceName([1-9][0-9]*)")


def read_saved_layout_device(source, configuration, cpu):
    path = Path("Physical") / configuration / "Hardware.hw"
    record = {"state": "unverified", "path": path.as_posix()}
    root, _ = source.xml(path, "Hardware")
    if root is None:
        return dict(record, reason="Could not read Hardware.hw")
    modules = [module for module in children(root, "Module") if module.get("Name") == cpu]
    if len(modules) != 1:
        return dict(record, reason="Expected one direct hardware module matching the selected CPU")
    module, = modules
    parameters = children(module, "Parameter")
    names = [item for item in parameters
             if SLOT_NAME.fullmatch(item.get("ID", "")) and item.get("Value") == DEVICE_NAME]
    if not names:
        return dict(record, state="absent")
    if len(names) != 1:
        return dict(record, reason="More than one slot names 6DData")
    slot = SLOT_NAME.fullmatch(names[0].get("ID"))[1]
    group = [item for item in children(module, "Group") if item.get("ID") == f"FileDevice{slot}"]
    paths = [item.get("Value") for item in parameters if item.get("ID") == f"FileDevicePath{slot}"]
    same_name_slot = [item for item in parameters if item.get("ID") == f"FileDeviceName{slot}"]
    if len(group) != 1 or len(same_name_slot) != 1 or len(paths) != 1 or not (paths[0] or "").strip():
        return dict(record, slot=slot, reason="The matching file-device slot has missing or duplicate fields")
    return dict(record, state="present", slot=slot, target=paths[0])


def read_compiled_layout_device(data):
    record = {"state": "unverified", "path": SYSTEM_CONFIG}
    start_marker, end_marker = b"<?xml", b"</tree>"
    if data.count(start_marker) != 1 or data.count(end_marker) != 1:
        return dict(record, reason="Expected one embedded ARsim system XML tree")
    start, stop = data.index(start_marker), data.index(end_marker) + len(end_marker)
    try:
        root = ET.fromstring(data[start:stop], parser=ET.XMLParser(target=ProjectXml()))
    except (ET.ParseError, LookupError, ValueError):
        return dict(record, reason="Could not parse the compiled ARsim device tree")
    containers = [node for node in children(root, "n") if node.get("ID") == "DEVICES"]
    if tag(root) != "tree" or len(containers) != 1:
        return dict(record, reason="Expected one DEVICES node in the compiled tree")
    devices = [node for node in children(containers[0], "n") if node.get("ID") == DEVICE_NAME]
    if not devices:
        return dict(record, state="absent")
    if len(devices) != 1:
        return dict(record, reason="More than one compiled device names 6DData")
    properties = children(devices[0], "d")
    paths = [(item.text or "") for item in properties if item.get("ID") == "DeviceParam"]
    drivers = [(item.text or "") for item in properties if item.get("ID") == "DDriverName"]
    if len(paths) != 1 or len(drivers) != 1 or not drivers[0] or not paths[0]:
        return dict(record, reason="The compiled 6DData node has incomplete or duplicate fields")
    return dict(record, state="present", deviceParameter=paths[0], driver=drivers[0])


def verify_packaged_layout_device(bundle):
    members = [member for member in bundle.infolist() if member.filename.casefold() == SYSTEM_CONFIG.casefold()]
    if len(members) != 1 or members[0].file_size > XML_LIMIT:
        raise ValueError(f"Expected one readable {SYSTEM_CONFIG} for the pinned {DEVICE_NAME} file device")
    data = bundle.read(members[0])
    compiled = read_compiled_layout_device(data)
    if compiled["state"] != "present":
        reason = compiled.get("reason", "The compiled 6DData node is absent")
        raise ValueError(reason)
    if compiled["deviceParameter"] != COMPILED_USER_PARTITION:
        raise ValueError(f"The compiled {DEVICE_NAME} needs {COMPILED_USER_PARTITION}, found {compiled['deviceParameter']!r}")
    return {"state": "matched", "compiled": compiled, "systemConfigSha256": sha256(data).hexdigest()}
