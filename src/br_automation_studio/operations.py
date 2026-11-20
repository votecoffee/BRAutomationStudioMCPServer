
"""Versioned operation definitions shared by automation clients."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .arsim_smoke import arsim_smoke
from .binding_review import BindingTarget
from .browser_smoke import ChromeProbe
from .hmi_smoke import arsim_hmi_smoke
from .inspection import inspect_project
from .launchpad_geometry import GeometryCriteria
from .launchpad_route import launchpad_route_review
from .native_build import execute_build, plan_build
from .opcua_smoke import OpcUaProbe
from .preflight import build_preflight
from .ruc_review import review_ruc
from .simulation_preflight import PAIRS, simulation_preflight

SCHEMA_VERSION = 1


class InvalidRequest(ValueError):
    """A request does not satisfy the operation contract."""


@dataclass(frozen=True)
class Operation:
    name: str
    summary: str
    arguments: dict
    required: tuple[str, ...]
    handler: Callable

    def request_schema(self):
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["schemaVersion", "workspace", "operation", "arguments"],
            "properties": {
                "schemaVersion": {"type": "integer", "const": SCHEMA_VERSION},
                "workspace": {"type": "string", "minLength": 1},
                "operation": {"const": self.name},
                "arguments": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(self.required),
                    "properties": self.arguments,
                },
            },
        }


def project_path(arguments, request_directory):
    path = request_directory / arguments["project"]
    if path.suffix.lower() != ".apj":
        raise InvalidRequest("project must point to an .apj file")
    return path


def inspect(arguments, request_directory):
    return inspect_project(project_path(arguments, request_directory), arguments.get("configuration"), arguments.get("cpu"))


def build_inputs(arguments, request_directory):
    runtime_root = arguments.get("runtimeRoot")
    return (
        project_path(arguments, request_directory),
        request_directory / arguments["studioRoot"],
        request_directory / runtime_root if runtime_root is not None else None,
        arguments.get("configuration"), arguments.get("cpu"),
    )


def preflight(arguments, request_directory):
    return build_preflight(*build_inputs(arguments, request_directory))


def run_simulation_preflight(arguments, request_directory):
    family = arguments["targetMapp6D"]
    if family not in PAIRS:
        raise InvalidRequest("targetMapp6D must be a supported AS4 family (2.4 or 2.7)")
    media = arguments.get("simulatorZip")
    export = arguments.get("launchPadExport")
    return simulation_preflight(
        *build_inputs(arguments, request_directory), family,
        request_directory / media if media is not None else None,
        request_directory / export if export is not None else None,
    )


def build_plan(arguments, request_directory):
    return plan_build(*build_inputs(arguments, request_directory))


def build_execute(arguments, request_directory):
    return execute_build(
        *build_inputs(arguments, request_directory),
        arguments["planSha256"], request_directory / arguments["jobDirectory"],
        arguments.get("timeoutSeconds", 180),
    )


def ruc_review(arguments, request_directory):
    return review_ruc(project_path(arguments, request_directory),
                      request_directory / arguments["jobDirectory"])


def run_launchpad_route_review(arguments, request_directory, geometry=None):
    name_map = arguments.get("nameMap")
    job = arguments.get("jobDirectory")
    return launchpad_route_review(
        project_path(arguments, request_directory), arguments.get("configuration"), arguments.get("cpu"),
        arguments["managerId"], request_directory / arguments["launchPadProject"],
        request_directory / arguments["launchPadReadback"],
        request_directory / name_map if name_map is not None else None,
        request_directory / job if job is not None else None,
        geometry=geometry,
    )


def run_launchpad_geometry_review(arguments, request_directory):
    orientation = arguments.get("expectedHandedness")
    if orientation is not None and orientation not in {"preserved", "reversed"}:
        raise InvalidRequest("expectedHandedness must be preserved or reversed")
    criteria = GeometryCriteria(arguments.get("distanceToleranceMicrometres"), orientation)
    return run_launchpad_route_review(arguments, request_directory, criteria)


def binding_target(arguments):
    return BindingTarget(arguments["visualizationId"], arguments["contentId"],
                         arguments["widgetId"], arguments["stringIdentifier"])


def ruc_binding_review(arguments, request_directory):
    return review_ruc(project_path(arguments, request_directory), request_directory / arguments["jobDirectory"],
                      binding_target(arguments))


def arsim_inputs(arguments, request_directory):
    return (
        project_path(arguments, request_directory),
        request_directory / arguments["jobDirectory"],
        request_directory / arguments["pviTransfer"],
        request_directory / arguments["sessionDirectory"],
        arguments.get("timeoutSeconds", 30),
    )


def run_arsim_smoke(arguments, request_directory, runtime_probe=None):
    return arsim_smoke(*arsim_inputs(arguments, request_directory), runtime_probe)


def run_arsim_browser_smoke(arguments, request_directory):
    probe = ChromeProbe(request_directory / arguments["browserExecutable"], arguments["counterWidgetId"],
                        arguments.get("assemblyWidgetId"), arguments.get("browserTimeoutSeconds", 30))
    return run_arsim_smoke(arguments, request_directory, probe)


def run_arsim_opcua_smoke(arguments, request_directory):
    probe = OpcUaProbe(arguments["namespaceUri"], arguments["stringIdentifier"],
                       arguments.get("opcUaTimeoutSeconds", 20))
    return run_arsim_smoke(arguments, request_directory, probe)


def run_arsim_hmi_smoke(arguments, request_directory):
    return arsim_hmi_smoke(
        *arsim_inputs(arguments, request_directory),
        binding_target(arguments), request_directory / arguments["browserExecutable"],
        arguments.get("assemblyWidgetId"), arguments.get("browserTimeoutSeconds", 30),
        arguments.get("opcUaTimeoutSeconds", 20),
    )


PROJECT_INSPECT = Operation(
    name="project_inspect",
    summary="Inspect saved project versions and ACOPOS 6D references.",
    arguments={
        "project": {"type": "string", "minLength": 1, "description": "Saved .apj path relative to the request or absolute."},
        "configuration": {"type": "string", "minLength": 1, "description": "Declared Physical configuration name."},
        "cpu": {"type": "string", "minLength": 1, "description": "Declared CPU in the selected configuration."},
    },
    required=("project",),
    handler=inspect,
)
BUILD_PREFLIGHT = Operation(
    name="build_preflight",
    summary="Compare a saved project with a selected AS4 installation and Runtime catalog.",
    arguments={
        **PROJECT_INSPECT.arguments,
        "studioRoot": {"type": "string", "minLength": 1, "description": "Automation Studio installation root."},
        "runtimeRoot": {"type": "string", "minLength": 1, "description": "Override the AS4 Runtime catalog under AS/System."},
    },
    required=("project", "studioRoot"),
    handler=preflight,
)
SIMULATION_PREFLIGHT = Operation(
    name="simulation_preflight",
    summary="Check a saved AS4 project, workstation and native export prerequisites for matched ACOPOS 6D simulation.",
    arguments={
        **BUILD_PREFLIGHT.arguments,
        "targetMapp6D": {"type": "string", "minLength": 3, "description": "Reviewed AS4 family, 2.4 or 2.7."},
        "simulatorZip": {"type": "string", "minLength": 1, "description": "Optional B&R simulator ZIP to match by SHA-256."},
        "launchPadExport": {"type": "string", "minLength": 1, "description": "Native LaunchPad .x6d for the intended route."},
    },
    required=(*BUILD_PREFLIGHT.required, "targetMapp6D"),
    handler=run_simulation_preflight,
)
BUILD_PLAN = Operation(
    name="build_plan",
    summary="Pin an offline AS4 PC_any simulation build to the source and installation read sets.",
    arguments=BUILD_PREFLIGHT.arguments,
    required=BUILD_PREFLIGHT.required,
    handler=build_plan,
)
BUILD_EXECUTE = Operation(
    name="build_execute",
    summary="Compile a pinned AS4 simulation plan on a private copy and validate its RUC archive.",
    arguments={
        **BUILD_PLAN.arguments,
        "planSha256": {"type": "string", "minLength": 64, "description": "SHA-256 from build_plan."},
        "jobDirectory": {"type": "string", "minLength": 1, "description": "Unused directory outside all input roots."},
        "timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 900, "default": 180},
    },
    required=(*BUILD_PLAN.required, "planSha256", "jobDirectory"),
    handler=build_execute,
)
RUC_REVIEW = Operation(
    name="ruc_review",
    summary="Verify a retained AS4 RUC job and compare its 6D modules, routes and widget links with saved sources.",
    arguments={
        "project": PROJECT_INSPECT.arguments["project"],
        "jobDirectory": {"type": "string", "minLength": 1, "description": "Directory holding the retained build receipts and RUC archive."},
    },
    required=("project", "jobDirectory"),
    handler=ruc_review,
)
LAUNCHPAD_ROUTE_REVIEW = Operation(
    name="launchpad_route_review",
    summary="Compare one saved LaunchPad cycle with the typed route of a selected saved or compiled Traffic Manager.",
    arguments={
        **PROJECT_INSPECT.arguments,
        "managerId": {"type": "string", "minLength": 1, "description": "Selected Traffic Manager element ID."},
        "launchPadProject": {"type": "string", "minLength": 1, "description": "Original saved LaunchPad .lpj."},
        "launchPadReadback": {"type": "string", "minLength": 1, "description": "JSON readback written by the local LaunchPad decoder."},
        "nameMap": {"type": "string", "minLength": 1, "description": "Optional JSON mapping of saved names to renamed manager objects."},
        "jobDirectory": RUC_REVIEW.arguments["jobDirectory"],
    },
    required=("project", "managerId", "launchPadProject", "launchPadReadback"),
    handler=run_launchpad_route_review,
)
LAUNCHPAD_GEOMETRY_REVIEW = Operation(
    name="launchpad_geometry_review",
    summary="Measure the saved or packaged station and waypoint geometry of a selected LaunchPad cycle.",
    arguments={
        **LAUNCHPAD_ROUTE_REVIEW.arguments,
        "distanceToleranceMicrometres": {"type": "integer", "minimum": 0, "maximum": 9007199254740991,
                                        "description": "Optional maximum absolute pairwise distance delta in micrometres."},
        "expectedHandedness": {"type": "string", "minLength": 1, "enum": ["preserved", "reversed"],
                               "description": "Optional expected handedness in the two recorded coordinate systems."},
    },
    required=LAUNCHPAD_ROUTE_REVIEW.required,
    handler=run_launchpad_geometry_review,
)
RUC_BINDING_REVIEW = Operation(
    name="ruc_binding_review",
    summary="Trace a one-way mapp View value binding from saved sources through a reviewed AS4 RUC.",
    arguments={
        **RUC_REVIEW.arguments,
        "visualizationId": {"type": "string", "minLength": 1, "description": "Saved visualization ID."},
        "contentId": {"type": "string", "minLength": 1, "description": "Saved content ID."},
        "widgetId": {"type": "string", "minLength": 1, "description": "Saved widget ID within that content."},
        "stringIdentifier": {"type": "string", "minLength": 1, "description": "Expected V1 OPC UA ::Task:Variable identifier."},
    },
    required=(*RUC_REVIEW.required, "visualizationId", "contentId", "widgetId", "stringIdentifier"),
    handler=ruc_binding_review,
)
ARSIM_SMOKE = Operation(
    name="arsim_smoke",
    summary="Materialize a reviewed AS4 RUC and verify its mapp View assets over owned loopback HTTP.",
    arguments={
        **RUC_REVIEW.arguments,
        "pviTransfer": {"type": "string", "minLength": 1, "description": "Path to installed PVI 4 PVITransfer.exe."},
        "sessionDirectory": {"type": "string", "minLength": 1, "description": "New private directory for the ARsim smoke session."},
        "timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 120, "default": 30},
    },
    required=(*RUC_REVIEW.required, "pviTransfer", "sessionDirectory"),
    handler=run_arsim_smoke,
)
ARSIM_BROWSER_SMOKE = Operation(
    name="arsim_browser_smoke",
    summary="Verify a reviewed mapp View HMI renders Assembly6D and refreshes a numeric counter on reopening.",
    arguments={
        **ARSIM_SMOKE.arguments,
        "browserExecutable": {"type": "string", "minLength": 1, "description": "Path to installed Google Chrome chrome.exe."},
        "counterWidgetId": {"type": "string", "minLength": 1, "description": "DOM ID of a read-only PLC-bound increasing counter widget."},
        "assemblyWidgetId": {"type": "string", "minLength": 1, "description": "DOM ID of a reviewed Assembly6D widget. Required when multiple are reviewed."},
        "browserTimeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 120, "default": 30},
    },
    required=(*ARSIM_SMOKE.required, "browserExecutable", "counterWidgetId"),
    handler=run_arsim_browser_smoke,
)
ARSIM_OPCUA_SMOKE = Operation(
    name="arsim_opcua_smoke",
    summary="Read a single increasing PLC counter from the owned ARsim OPC UA listener and check access metadata.",
    arguments={
        **ARSIM_SMOKE.arguments,
        "namespaceUri": {"type": "string", "minLength": 1, "description": "Exact OPC UA namespace URI."},
        "stringIdentifier": {"type": "string", "minLength": 1, "description": "Exact OPC UA string node identifier."},
        "opcUaTimeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 120, "default": 20},
    },
    required=(*ARSIM_SMOKE.required, "namespaceUri", "stringIdentifier"),
    handler=run_arsim_opcua_smoke,
)
ARSIM_HMI_SMOKE = Operation(
    name="arsim_hmi_smoke",
    summary="Verify a compiled counter binding and compare direct OPC UA and browser values in one ARsim run.",
    arguments={
        **RUC_BINDING_REVIEW.arguments,
        **ARSIM_SMOKE.arguments,
        "browserExecutable": ARSIM_BROWSER_SMOKE.arguments["browserExecutable"],
        "assemblyWidgetId": ARSIM_BROWSER_SMOKE.arguments["assemblyWidgetId"],
        "browserTimeoutSeconds": ARSIM_BROWSER_SMOKE.arguments["browserTimeoutSeconds"],
        "opcUaTimeoutSeconds": ARSIM_OPCUA_SMOKE.arguments["opcUaTimeoutSeconds"],
    },
    required=(*ARSIM_SMOKE.required, "visualizationId", "contentId", "widgetId", "stringIdentifier", "browserExecutable"),
    handler=run_arsim_hmi_smoke,
)
OPERATIONS = {item.name: item for item in (
    PROJECT_INSPECT, BUILD_PREFLIGHT, SIMULATION_PREFLIGHT, BUILD_PLAN, BUILD_EXECUTE, RUC_REVIEW,
    LAUNCHPAD_ROUTE_REVIEW, LAUNCHPAD_GEOMETRY_REVIEW, RUC_BINDING_REVIEW,
    ARSIM_SMOKE, ARSIM_BROWSER_SMOKE, ARSIM_OPCUA_SMOKE, ARSIM_HMI_SMOKE,
)}


def valid_text(value):
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def execute(request, request_directory: Path):
    if not isinstance(request, dict) or request.keys() != {"schemaVersion", "workspace", "operation", "arguments"}:
        raise InvalidRequest("Request requires schemaVersion, workspace, operation and arguments")
    version = request["schemaVersion"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise InvalidRequest(f"Expected schemaVersion {SCHEMA_VERSION}")
    if not valid_text(request["workspace"]):
        raise InvalidRequest("workspace must be a nonempty string")
    name = request["operation"]
    if not isinstance(name, str) or name not in OPERATIONS:
        raise InvalidRequest("Unknown operation. Use operations to list supported commands.")
    operation = OPERATIONS[name]
    arguments = request["arguments"]
    if not isinstance(arguments, dict):
        raise InvalidRequest("arguments must be an object")
    if unknown := arguments.keys() - operation.arguments.keys():
        raise InvalidRequest(f"Unknown arguments: {', '.join(sorted(unknown))}")
    if missing := set(operation.required) - arguments.keys():
        raise InvalidRequest(f"Missing arguments: {', '.join(sorted(missing))}")
    for key, value in arguments.items():
        schema = operation.arguments[key]
        if schema["type"] == "integer":
            if type(value) is not int or not schema["minimum"] <= value <= schema["maximum"]:
                raise InvalidRequest(f"{key} must be an integer between {schema['minimum']} and {schema['maximum']}")
        elif not valid_text(value) or len(value) < schema["minLength"]:
            raise InvalidRequest(f"{key} must be a nonempty string of at least {schema['minLength']} characters")

    result = operation.handler(arguments, request_directory)
    severities = {item["severity"] for item in result["diagnostics"]}
    status = "issues" if "error" in severities else "partial" if "warning" in severities else "complete"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "workspace": request["workspace"],
        "operation": name,
        "status": status,
        "result": result,
    }
