"""Review the saved CPU and assembly simulation settings for a physical handoff."""

from pathlib import Path

from .hardware_timing import exact_cpu_catalog, parameter_values, selected_cpu_module, single_value
from .inspection import registered_assembly
from .project import children

SIMULATION_GROUP = Path("AS/Hardware/ArCfgGroupSimulation.xml")


def cpu_default(studio, catalog, runtime_supports):
    if catalog is None:
        return None
    module, _ = studio.xml(catalog, "Module")
    includes = [item for section in children(module, "Properties") if section.get("Target") == "SG4"
                for item in children(section, "Include") if item.get("File") == SIMULATION_GROUP.stem]
    if len(includes) != 1 or includes[0].attrib != {"File": SIMULATION_GROUP.stem}:
        return None

    definition, _ = studio.xml(SIMULATION_GROUP, "Configuration", optional=True)
    if definition is None:
        return None
    groups = [node for node in children(definition, "Group") if node.get("ID") == "AR000"]
    if len(groups) != 1:
        return None
    group, = groups
    parameters = [node for node in children(group, "Parameter") if node.get("ID") == "Simulation"]
    if len(parameters) != 1:
        return None
    parameter, = parameters
    if not all(runtime_supports(node.get("FromVersion"), node.get("ToVersion")) for node in (group, parameter)):
        return None
    return parameter.get("Value")


def review_physical_assembly(saved, studio, project, cpu_catalog, assembly, runtime_supports):
    cpu_type = project["cpu"].get("moduleId")
    result = {"assembly": assembly, "state": "unverified", "assemblyPath": None, "assemblySimulation": None,
              "cpuModuleId": cpu_type, "hardware": None, "cpuSimulation": None, "cpuSimulationSource": None,
              "cpuSimulationSourcePath": None, "cpuCatalog": None}
    if not project["selectedCpu"]:
        return result

    before = len(saved.diagnostics)

    def issue(path, message, severity="warning"):
        code = "physical_assembly_mode_mismatch" if severity == "error" else "physical_assembly_mode_unverified"
        saved.issue(severity, code, path, message)
        if severity == "error":
            result["state"] = "mismatched"

    path, element = registered_assembly(saved, project, assembly)
    if path is None:
        issue(Path(project["project"]), f"Could not identify one registered 6D assembly {assembly!r} for the selected CPU")
    else:
        result["assemblyPath"] = path.as_posix()
        mode = single_value([field.get("Value") for field in children(element, "Property")
                             if field.get("ID") == "Simulation6D"])
        result["assemblySimulation"] = mode
        if mode == "SimOn":
            issue(path, "The saved 6D assembly has simulation enabled", "error")
        elif mode != "SimOff":
            issue(path, "The assembly needs one explicit Simulation6D setting of SimOff for the physical check")

    if cpu_type == "PC_any":
        cpu_path = Path("Physical", project["selectedConfiguration"], project["selectedCpu"], "Cpu.pkg")
        result["cpuSimulationSource"] = "cpu_type"
        result["cpuSimulationSourcePath"] = cpu_path.as_posix()
        issue(cpu_path, "The selected PC_any CPU targets Automation Runtime simulation", "error")
        return result

    hardware = Path("Physical", project["selectedConfiguration"], "Hardware.hw")
    result["hardware"] = hardware.as_posix()
    root, _ = saved.xml(hardware, "Hardware")
    if root is None:
        return result
    cpu = selected_cpu_module(root, project["selectedCpu"], cpu_type)
    if cpu is None:
        issue(hardware, "Could not identify one top-level hardware module for the selected CPU")
        return result

    values = parameter_values(cpu, "Simulation")
    source_path = hardware
    if values:
        value = single_value(values)
        result["cpuSimulationSource"] = "saved"
    else:
        catalog = exact_cpu_catalog(cpu, cpu_catalog)
        result["cpuCatalog"] = catalog.as_posix() if catalog is not None else None
        value = cpu_default(studio, catalog, runtime_supports)
        if value is not None:
            source_path = SIMULATION_GROUP
            result["cpuSimulationSource"] = "catalog_default"

    result["cpuSimulation"] = value
    if result["cpuSimulationSource"] is not None:
        result["cpuSimulationSourcePath"] = source_path.as_posix()
    if value == "1":
        if source_path == hardware:
            issue(hardware, "The selected CPU has simulation enabled in Hardware.hw", "error")
        else:
            result["state"] = "mismatched"
            studio.issue("error", "physical_assembly_mode_mismatch", source_path,
                         "The selected CPU's installed catalog default enables simulation")
    elif value != "0":
        issue(hardware, "Could not verify a saved CPU Simulation value or an applicable exact catalog default")
    if result["state"] != "mismatched" and len(saved.diagnostics) == before:
        result["state"] = "matched"
    return result
