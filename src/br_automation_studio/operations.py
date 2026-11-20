
"""Versioned operation definitions shared by automation clients."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .arsim_smoke import arsim_smoke
from .inspection import inspect_project
from .native_build import execute_build, plan_build
from .preflight import build_preflight
from .ruc_review import review_ruc

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


def run_arsim_smoke(arguments, request_directory):
    return arsim_smoke(
        project_path(arguments, request_directory),
        request_directory / arguments["jobDirectory"],
        request_directory / arguments["pviTransfer"],
        request_directory / arguments["sessionDirectory"],
        arguments.get("timeoutSeconds", 30),
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
OPERATIONS = {item.name: item for item in (
    PROJECT_INSPECT, BUILD_PREFLIGHT, BUILD_PLAN, BUILD_EXECUTE, RUC_REVIEW, ARSIM_SMOKE,
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
