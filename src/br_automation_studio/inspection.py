
"""Inspect saved project selections and assembly links without running vendor tools."""

from pathlib import Path
from .links import MotionLinks
from .project import MOTION_TYPES, SavedFiles, children, choices, package_files, select, tag


def inspect_project(path, configuration=None, cpu=None):
    path = Path(path)
    return inspect_saved(SavedFiles(path.parent), Path(path.name), configuration, cpu)


def inspect_saved(source, project, configuration=None, cpu=None):
    node, studio = source.xml(project, "Project")
    packages = []
    if node is not None:
        for container in children(node, "TechnologyPackages"):
            for package in container:
                packages.append({
                    "name": tag(package),
                    "version": package.get("Version"),
                    "subversions": {key: value for key, value in package.attrib.items() if key != "Version"},
                })

    selected_configuration = None
    selected_cpu = None
    configurations = {}
    cpus = {}
    cpu_details = {}
    assembly_links, managers, widgets, modules = [], [], [], []
    if node is not None:
        physical = Path("Physical/Physical.pkg")
        physical_node, _ = source.xml(physical, "Physical")
        if physical_node is not None:
            configurations = choices(source, physical, physical_node, "Configuration")
            selected_configuration = select(source, physical, configurations, configuration, "configuration")
        if selected_configuration is not None:
            config_path = selected_configuration / "Config.pkg"
            config_node, _ = source.xml(config_path, "Configuration")
            if config_node is not None:
                cpus = choices(source, config_path, config_node, "Cpu")
                selected_cpu = select(source, config_path, cpus, cpu, "CPU")
        if selected_cpu is not None:
            cpu_path = selected_cpu / "Cpu.pkg"
            cpu_node, _ = source.xml(cpu_path, "Cpu")
            if cpu_node is not None:
                for detail in children(cpu_node, "Configuration"):
                    cpu_details["moduleId"] = detail.get("ModuleId")
                    for runtime in children(detail, "AutomationRuntime"):
                        cpu_details["runtimeVersion"] = runtime.get("Version")
                motion = MotionLinks(source)
                for path in package_files(source, cpu_path, "Cpu", MOTION_TYPES):
                    module, _ = source.xml(path, "Configuration")
                    if module is not None:
                        motion.add(path, path.suffix.lower()[1:], module)
                assembly_links, managers = motion.check()
                content = package_files(source, Path("Logical/Package.pkg"), "Package", {"content"})
                widgets = list(motion.widgets(content, managers))
                modules = motion.module_inventory

    return {
        "evidence": "saved_files",
        "project": project.name,
        "automationStudio": studio,
        "technologyPackages": packages,
        "configurations": [item.name for item in configurations.values()],
        "selectedConfiguration": selected_configuration.name if selected_configuration else None,
        "cpus": [item.name for item in cpus.values()],
        "selectedCpu": selected_cpu.name if selected_cpu else None,
        "cpu": cpu_details,
        "motionModules": modules,
        "assemblyLinks": assembly_links,
        "trafficManagers": managers,
        "widgets": widgets,
        "snapshot": source.snapshot(),
        "diagnostics": source.diagnostics,
    }
