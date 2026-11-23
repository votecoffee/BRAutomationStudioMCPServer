"""Review explicit timing and a direct ACOPOS 6D POWERLINK connection in AS4 saved files."""

from pathlib import Path
import re

from .inspection import registered_assembly
from .project import children


def selected_cpu_module(hardware, name, module_id):
    cpus = [node for node in children(hardware, "Module")
            if node.get("Name") == name and node.get("Type") == module_id]
    return cpus[0] if len(cpus) == 1 else None


def exact_cpu_catalog(cpu, entries):
    revision = cpu.get("Version")
    matches = [row["path"] for row in entries if row["version"] == revision
               and Path(row["path"]).parent.name == revision]
    return Path(matches[0]) if len(matches) == 1 else None


def single_value(values):
    return (values[0] or None) if len(values) == 1 else None


def parameter_values(node, key):
    return [param.get("Value") for param in children(node, "Parameter") if param.get("ID") == key]


def unsigned_value(value):
    if value and re.fullmatch(r"[0-9]{1,10}", value) and int(value) <= 0xFFFFFFFF:
        return int(value)
    return None


def powerlink_ports(definition):
    return [port.get("Name") for group in children(definition, "Connectors")
            for port in children(group, "Connector")
            if any(link.get("Name") == "Powerlink" for protocol in children(port, "Connections")
                   for link in children(protocol, "Connection"))]


def controlled_node(value):
    number = unsigned_value(value)
    return number if number is not None and 1 <= number <= 239 else None


def review_assembly_address(saved, project, assembly, connector, hardware_path, node_values):
    result = {"assembly": assembly, "state": "unverified", "assemblyPath": None,
              "assemblyInterface": None, "assemblyNodeNumber": None, "hardwareNodeNumber": None}
    path, element = registered_assembly(saved, project, assembly)
    if path is None:
        saved.issue("warning", "controller_powerlink_address_unverified", Path(project["project"]),
                    f"Could not identify one registered 6D assembly {assembly!r} for the selected CPU")
        return result

    result["assemblyPath"] = path.as_posix()
    groups = [group for group in children(element, "Group") if group.get("ID") == "Fieldbus6D"]
    fields = children(groups[0], "Property") if len(groups) == 1 else []
    interface = single_value([field.get("Value") for field in fields if field.get("ID") == "IFName"])
    node = controlled_node(single_value([field.get("Value") for field in fields if field.get("ID") == "NodeNumber"]))
    result["assemblyInterface"] = interface
    result["assemblyNodeNumber"] = node

    numbers = {controlled_node(value) for value in node_values}
    if len(numbers - {None}) > 1:
        result["state"] = "mismatched"
        saved.issue("error", "controller_powerlink_address_mismatch", hardware_path,
                    "The controller module and its direct connection declare different node numbers")
        return result
    if len(numbers) == 1:
        result["hardwareNodeNumber"], = numbers

    hardware_node = result["hardwareNodeNumber"]
    interface_recognized = interface == connector or re.fullmatch(r"IF[0-9]+", interface or "") is not None
    mismatch = []
    if interface_recognized and interface != connector:
        mismatch.append(f"The assembly interface is {interface!r} and the hardware connection uses {connector!r}.")
    if node is not None and hardware_node is not None and node != hardware_node:
        mismatch.append(f"The assembly node number is {node} and the saved hardware node is {hardware_node}.")
    if mismatch:
        result["state"] = "mismatched"
        saved.issue("error", "controller_powerlink_address_mismatch", path, " ".join(mismatch))
    elif node is None or hardware_node is None or not interface_recognized:
        saved.issue("warning", "controller_powerlink_address_unverified", path,
                    "Expected an explicit Fieldbus6D IFName on the assembly and a decimal NodeNumber "
                    "from 1 through 239 on both the assembly and its saved hardware")
    else:
        result["state"] = "matched"
    return result


def review_powerlink(saved, studio, project, hardware, cpu_catalog, connector, assembly=None):
    result = {"state": "unverified", "hardware": hardware["hardware"], "connector": connector,
              "controller": None, "controllerPort": None, "cpuCatalog": None,
              "timerType": None, "timerDevice": None, "cycleMicroseconds": None}
    if assembly is not None:
        result["assemblyAddress"] = {"assembly": assembly, "state": "unverified"}
    if hardware["state"] != "matched" or not project["selectedCpu"]:
        return result

    path = Path(hardware["hardware"])
    diagnostics_before = len(saved.diagnostics)

    def issue(message, severity="warning"):
        code = "controller_powerlink_mismatch" if severity == "error" else "controller_powerlink_unverified"
        saved.issue(severity, code, path, message)
        if severity == "error":
            result["state"] = "mismatched"

    if len(hardware["modules"]) != 1:
        issue("Select a configuration with one matched 6D controller for the direct POWERLINK review")
        return result

    root, _ = saved.xml(path, "Hardware")
    cpu_name = project["selectedCpu"]
    cpu = selected_cpu_module(root, cpu_name, project["cpu"].get("moduleId"))
    if cpu is None:
        issue("Could not identify one top-level hardware module for the selected CPU")
        return result

    exact = exact_cpu_catalog(cpu, cpu_catalog)
    if exact is None:
        issue("Could not verify the CPU connector against its exact saved hardware revision")
        return result
    result["cpuCatalog"] = exact.as_posix()
    cpu_definition, _ = studio.xml(exact, "Module")
    if powerlink_ports(cpu_definition).count(connector) != 1:
        issue(f"The exact CPU catalog does not declare one POWERLINK connector named {connector!r}", "error")
        return result

    selected = hardware["modules"][0]
    result["controller"] = selected["name"]
    controller_definition, _ = studio.xml(Path(selected["catalog"]), "Module")
    controller_ports = powerlink_ports(controller_definition)
    direct = [(node, link) for node in children(root, "Module")
              if node.get("Name") == selected["name"] and node.get("Type") == selected["type"]
              for link in children(node, "Connection")
              if link.get("Connector") in controller_ports and link.get("TargetModule") == cpu_name]
    if len(direct) != 1:
        issue("Could not verify one direct saved connection from the controller to the selected CPU")
    else:
        (device, link), = direct
        if link.get("TargetConnector") != connector:
            issue(f"The saved controller connection targets {link.get('TargetConnector')!r} instead of {connector!r}",
                  "error")
        else:
            result["controllerPort"] = link.get("Connector")
            if assembly is not None:
                numbers = [value for item in (device, link) if (value := item.get("NodeNumber")) is not None]
                address = review_assembly_address(saved, project, assembly, connector, path, numbers)
                result["assemblyAddress"] = address
                if address["state"] == "mismatched":
                    result["state"] = "mismatched"

    result["timerType"] = single_value(parameter_values(cpu, "TimerDeviceType"))
    result["timerDevice"] = single_value(parameter_values(cpu, "TimerDevice"))
    expected_timer = f"{cpu_name}.{connector}"
    if (result["timerType"] not in (None, "EPLX2X")
            or result["timerDevice"] not in (None, expected_timer)):
        issue(f"The saved CPU system timer does not select EPL/X2X with {expected_timer!r}", "error")
    elif None in (result["timerType"], result["timerDevice"]):
        issue("The CPU has no single explicit timer type and device")

    ports = [port for port in children(cpu, "Connector") if port.get("Name") == connector]
    if len(ports) != 1:
        issue(f"The CPU has no single saved connector block for {connector!r}")
        return result

    port, = ports
    cycle = unsigned_value(single_value(parameter_values(port, "CycleTime")))
    result["cycleMicroseconds"] = cycle
    if not cycle:
        issue(f"{connector} has no single positive saved CycleTime in microseconds")
    for key, expected in (("Operation", "EPL-V2"), ("ActivatePowerlink", "1"), ("Mode", "MN")):
        values = parameter_values(port, key)
        if values and single_value(values) is None:
            issue(f"{connector} has ambiguous saved {key} settings")
        elif values and values[0] != expected:
            issue(f"{connector} explicitly selects {key}={values[0]!r} instead of {expected!r}", "error")
    if len(saved.diagnostics) == diagnostics_before:
        result["state"] = "matched"
    return result
