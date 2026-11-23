"""Review explicit timing and a direct ACOPOS 6D POWERLINK connection in AS4 saved files."""

from pathlib import Path
import re

from .project import children


def selected_cpu_module(hardware, name, module_id):
    cpus = [node for node in children(hardware, "Module")
            if node.get("Name") == name and node.get("Type") == module_id]
    return cpus[0] if len(cpus) == 1 else None


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


def review_powerlink(saved, studio, project, hardware, cpu_catalog, connector):
    result = {"state": "unverified", "hardware": hardware["hardware"], "connector": connector,
              "controller": None, "controllerPort": None, "cpuCatalog": None,
              "timerType": None, "timerDevice": None, "cycleMicroseconds": None}
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

    version = cpu.get("Version")
    exact = [row for row in cpu_catalog if row["version"] == version and Path(row["path"]).parent.name == version]
    if len(exact) != 1:
        issue("Could not verify the CPU connector against its exact saved hardware revision")
        return result
    result["cpuCatalog"] = exact[0]["path"]
    cpu_definition, _ = studio.xml(Path(result["cpuCatalog"]), "Module")
    if powerlink_ports(cpu_definition).count(connector) != 1:
        issue(f"The exact CPU catalog does not declare one POWERLINK connector named {connector!r}", "error")
        return result

    selected = hardware["modules"][0]
    result["controller"] = selected["name"]
    controller_definition, _ = studio.xml(Path(selected["catalog"]), "Module")
    controller_ports = powerlink_ports(controller_definition)
    direct = [link for node in children(root, "Module")
              if node.get("Name") == selected["name"] and node.get("Type") == selected["type"]
              for link in children(node, "Connection")
              if link.get("Connector") in controller_ports and link.get("TargetModule") == cpu_name]
    if len(direct) != 1:
        issue("Could not verify one direct saved connection from the controller to the selected CPU")
    elif direct[0].get("TargetConnector") != connector:
        issue(f"The saved controller connection targets {direct[0].get('TargetConnector')!r} instead of {connector!r}",
              "error")
    else:
        result["controllerPort"] = direct[0].get("Connector")

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
