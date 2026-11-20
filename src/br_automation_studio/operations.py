
"""Versioned operation definitions shared by automation clients."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .inspection import inspect_project

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


def inspect(arguments, request_directory):
    path = request_directory / arguments["project"]
    if path.suffix.lower() != ".apj":
        raise InvalidRequest("project must point to an .apj file")
    return inspect_project(path, arguments.get("configuration"), arguments.get("cpu"))


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
OPERATIONS = {operation.name: operation for operation in (PROJECT_INSPECT,)}


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
        if not valid_text(value):
            raise InvalidRequest(f"{key} must be a nonempty string")

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
