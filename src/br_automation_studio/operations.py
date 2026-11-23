
"""Versioned operation definitions shared by automation clients."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .arsim_smoke import arsim_smoke
from .assembly_io import assembly_io_review
from .binding_review import BindingTarget, review_saved_binding
from .browser_smoke import ChromeProbe
from .cycle_trace import RELEASE_TOTAL_EVENTS, cycle_trace_review
from .hmi_smoke import arsim_hmi_smoke, read_status_bindings
from .inspection import inspect_project
from .layout_device import layout_device_review
from .layout_svg import layout_svg_review
from .launchpad_build import LaunchPadBuildInputs
from .launchpad_export import review_launchpad_export
from .launchpad_frame import launchpad_frame_review
from .launchpad_geometry import GeometryCriteria
from .launchpad_route import launchpad_route_review
from .links import READ_AXES
from .motion_interfaces import FIELD_PATH, SYMBOL, review_motion_interfaces
from .native_build import execute_build, plan_build
from .opcua_smoke import OpcUaProbe
from .preflight import CATALOG_VERSION, build_preflight, numeric_version
from .ruc_review import review_ruc
from .runtime_capture import RuntimeScanCapture
from .simulation_preflight import (COMPANION_MEDIA_27, PAIRS, SIMULATION_PACKAGES, simulation_environment,
                                   simulation_preflight)
from .start_page import StartPageWidget, review_saved_assembly_widget
from .station_trace import station_trace_review, trace_decimal
from .widget_packages import widget_package_review

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


def run_widget_package_review(arguments, request_directory):
    job = arguments.get("jobDirectory")
    return widget_package_review(project_path(arguments, request_directory),
                                 request_directory / job if job is not None else None)


def preflight(arguments, request_directory):
    assembly = arguments.get("controllerIoAssembly")
    controller = arguments.get("expectedControllerType")
    task_class = arguments.get("controllerIoTaskClass")
    powerlink = arguments.get("controllerPowerlinkConnector")
    powerlink_assembly = arguments.get("controllerPowerlinkAssembly")
    if assembly is not None and controller is None:
        raise InvalidRequest("controllerIoAssembly needs expectedControllerType")
    if task_class is not None and assembly is None:
        raise InvalidRequest("controllerIoTaskClass needs controllerIoAssembly")
    if powerlink is not None and controller is None:
        raise InvalidRequest("controllerPowerlinkConnector needs expectedControllerType")
    if powerlink_assembly is not None and powerlink is None:
        raise InvalidRequest("controllerPowerlinkAssembly needs controllerPowerlinkConnector")
    return build_preflight(*build_inputs(arguments, request_directory),
                           verify_motion_declarations=arguments.get("verifyMotionDeclarations", False),
                           expected_controller_type=controller, controller_io_assembly=assembly,
                           controller_io_task_class=task_class, controller_powerlink_connector=powerlink,
                           controller_powerlink_assembly=powerlink_assembly,
                           physical_assembly=arguments.get("physicalAssembly"),
                           mapp_view_ethernet_connector=arguments.get("mappViewEthernetConnector"))


def run_motion_interface_review(arguments, request_directory):
    blocks, enums = arguments.get("blocks", ()), arguments.get("enumTypes", ())
    if not blocks and not enums:
        raise InvalidRequest("Supply blocks or enumTypes")
    for key, names in (("blocks", blocks), ("enumTypes", enums)):
        if any(SYMBOL.fullmatch(name) is None for name in names) or len({name.casefold() for name in names}) != len(names):
            raise InvalidRequest(f"{key} must contain distinct IEC names")
    paths = arguments.get("fieldPaths", ())
    labels = {name.casefold() for name in blocks}
    if any(FIELD_PATH.fullmatch(path) is None or path.partition(".")[0].casefold() not in labels for path in paths):
        raise InvalidRequest("fieldPaths must name a selected block and its pin and STRUCT fields")
    if len({path.casefold() for path in paths}) != len(paths):
        raise InvalidRequest("fieldPaths must contain distinct field paths")
    return review_motion_interfaces(*build_inputs(arguments, request_directory), blocks, paths, enums)


def simulation_companion_media(arguments, request_directory):
    return {role: request_directory / arguments[role] for role in COMPANION_MEDIA_27 if role in arguments}


def run_simulation_environment(arguments, request_directory):
    media = arguments.get("simulatorZip")
    selectors = {name: arguments[f"{name}Selector"] for name in SIMULATION_PACKAGES if f"{name}Selector" in arguments}
    if selectors and (len(selectors) != len(SIMULATION_PACKAGES) or any(
            CATALOG_VERSION.fullmatch(value) is None or numeric_version(value) is None for value in selectors.values())):
        raise InvalidRequest("Supply all three numeric package selectors together: mapp6DSelector, "
                             "mappMotionSelector and mappViewSelector")
    return simulation_environment(request_directory / arguments["studioRoot"], arguments["targetMapp6D"],
                                  request_directory / media if media is not None else None,
                                  companion_media=simulation_companion_media(arguments, request_directory),
                                  package_selectors=selectors or None)


def environment_report_path(arguments, request_directory):
    report = arguments.get("environmentReport")
    return request_directory / report if report is not None else None


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
        verify_mptraffic_enum=arguments.get("verifyMpTrafficEnum", False),
        verify_motion_declarations=arguments.get("verifyMotionDeclarations", False),
        companion_media=simulation_companion_media(arguments, request_directory),
        inspect_launchpad_export=arguments.get("inspectLaunchPadExport", False),
        environment_report=environment_report_path(arguments, request_directory),
    )


LAUNCHPAD_ROUTE_INPUTS = {
    "launchPadProject": {"type": "string", "minLength": 1, "description": "Original saved LaunchPad .lpj."},
    "launchPadReadback": {"type": "string", "minLength": 1, "description": "JSON readback written by the local LaunchPad decoder."},
    "managerId": {"type": "string", "minLength": 1, "description": "Selected Traffic Manager element ID."},
    "nameMap": {"type": "string", "minLength": 1, "description": "Optional JSON mapping of saved names to renamed manager objects."},
}


def launchpad_build_inputs(arguments, request_directory):
    required = {"launchPadProject", "launchPadReadback", "launchPadExport", "managerId"}
    verify_layout = arguments.get("verifyLaunchPadLayout", False)
    verify_positions = arguments.get("verifyLaunchPadPositions", False)
    widget = arguments.get("launchPadWidgetId")
    verify_rules = arguments.get("verifyLaunchPadRules", False)
    verify_recovery = arguments.get("verifyLaunchPadRecovery", False)
    verify_motion_limits = arguments.get("verifyLaunchPadMotionLimits", False)
    read_axes = arguments.get("launchPadReadAxes")
    if not (verify_layout or verify_positions or verify_rules or verify_recovery or verify_motion_limits or widget is not None
            or read_axes is not None or arguments.keys() & (required | {"nameMap"})):
        return None
    if missing := required - arguments.keys():
        raise InvalidRequest(f"Missing LaunchPad build inputs: {', '.join(sorted(missing))}")
    if read_axes is not None and (any(axis not in READ_AXES for axis in read_axes) or len(set(read_axes)) != len(read_axes)):
        raise InvalidRequest("launchPadReadAxes needs distinct axis names from X, Y, Z, Rx, Ry and Rz")
    mapping = arguments.get("nameMap")
    return LaunchPadBuildInputs(request_directory / arguments["launchPadProject"],
                                request_directory / arguments["launchPadReadback"],
                                request_directory / arguments["launchPadExport"], arguments["managerId"],
                                request_directory / mapping if mapping is not None else None,
                                verify_layout=verify_layout, verify_positions=verify_positions, widget_id=widget,
                                verify_rules=verify_rules, verify_recovery=verify_recovery,
                                verify_motion_limits=verify_motion_limits, read_axes=read_axes)


def build_plan(arguments, request_directory):
    return plan_build(*build_inputs(arguments, request_directory),
                      verify_mptraffic_enum=arguments.get("verifyMpTrafficEnum", False),
                      verify_motion_payloads=arguments.get("verifyMotionPayloads", False),
                      verify_motion_declarations=arguments.get("verifyMotionDeclarations", False),
                      verify_motion_symbols=arguments.get("verifyMotionSymbols", False),
                      launchpad_inputs=launchpad_build_inputs(arguments, request_directory),
                      environment_report=environment_report_path(arguments, request_directory))


def build_execute(arguments, request_directory):
    return execute_build(
        *build_inputs(arguments, request_directory),
        arguments["planSha256"], request_directory / arguments["jobDirectory"],
        arguments.get("timeoutSeconds", 180), verify_mptraffic_enum=arguments.get("verifyMpTrafficEnum", False),
        verify_motion_payloads=arguments.get("verifyMotionPayloads", False),
        verify_motion_declarations=arguments.get("verifyMotionDeclarations", False),
        verify_motion_symbols=arguments.get("verifyMotionSymbols", False),
        launchpad_inputs=launchpad_build_inputs(arguments, request_directory),
        environment_report=environment_report_path(arguments, request_directory),
    )


def ruc_review(arguments, request_directory):
    widget = arguments.get("startPageWidgetId")
    return review_ruc(project_path(arguments, request_directory),
                      request_directory / arguments["jobDirectory"],
                      start_widget=StartPageWidget(widget) if widget is not None else None)


def run_assembly_io_review(arguments, request_directory):
    job = arguments.get("jobDirectory")
    return assembly_io_review(project_path(arguments, request_directory), arguments.get("configuration"),
                              arguments.get("cpu"), arguments["assemblyId"],
                              request_directory / job if job is not None else None)


def saved_assembly_widget_review(arguments, request_directory):
    return review_saved_assembly_widget(project_path(arguments, request_directory), arguments.get("configuration"),
                                       arguments.get("cpu"), arguments.get("startPageWidgetId"),
                                       arguments["assemblyId"], arguments["managerId"])


def run_layout_device_review(arguments, request_directory):
    job = arguments.get("jobDirectory")
    return layout_device_review(project_path(arguments, request_directory), arguments.get("configuration"),
                                arguments.get("cpu"), request_directory / job if job is not None else None)


def run_layout_svg_review(arguments, request_directory):
    status_report = arguments.get("hmiStatusReport")
    assembly, traffic = (arguments.get(key) for key in ("assemblySvg", "trafficSvg"))
    if (status_report and (assembly or traffic)) or (not status_report and not (assembly and traffic)):
        raise InvalidRequest("Supply hmiStatusReport or both assemblySvg and trafficSvg")
    return layout_svg_review(project_path(arguments, request_directory), request_directory / arguments["jobDirectory"],
                             arguments["managerId"], request_directory / assembly if assembly else None,
                             request_directory / traffic if traffic else None,
                             hmi_status_path=request_directory / status_report if status_report else None,
                             verify_mapping_line_endpoints=arguments.get("verifyMappingLineEndpoints", False),
                             verify_traffic_labels=arguments.get("verifyTrafficLabels", False))


def run_launchpad_export_review(arguments, request_directory):
    return review_launchpad_export(request_directory / arguments["launchPadProject"],
                                  request_directory / arguments["launchPadReadback"],
                                  request_directory / arguments["launchPadExport"])


def launchpad_inputs(arguments, request_directory):
    name_map = arguments.get("nameMap")
    job = arguments.get("jobDirectory")
    return (
        project_path(arguments, request_directory), arguments.get("configuration"), arguments.get("cpu"),
        arguments["managerId"], request_directory / arguments["launchPadProject"],
        request_directory / arguments["launchPadReadback"],
        request_directory / name_map if name_map is not None else None,
        request_directory / job if job is not None else None,
    )


def run_launchpad_route_review(arguments, request_directory, geometry=None, *, include_layout=False):
    return launchpad_route_review(*launchpad_inputs(arguments, request_directory), geometry=geometry,
                                  include_layout=include_layout)


def run_launchpad_station_review(arguments, request_directory):
    return launchpad_route_review(*launchpad_inputs(arguments, request_directory), include_stations=True)


def expected_release_motion(arguments):
    fields = {"expectedExitVelocityMps": "exitVelocityMps", "expectedExitAccelerationMps2": "exitAccelerationMps2"}
    expected = {name: arguments[key] for key, name in fields.items() if key in arguments}
    if expected and len(expected) != len(fields):
        raise InvalidRequest("Supply both expectedExitVelocityMps and expectedExitAccelerationMps2")
    for value in expected.values():
        try:
            trace_decimal(value)
        except ValueError as error:
            raise InvalidRequest(str(error)) from error
    return expected or None


def sampled_speed_options(arguments):
    verify = arguments.get("verifySampledSpeed", False)
    interval = arguments.get("maxSampleIntervalMicroseconds")
    if interval is not None and not verify:
        raise InvalidRequest("maxSampleIntervalMicroseconds requires verifySampledSpeed to be true")
    return {"verify_sampled_speed": verify, "max_sample_interval_us": interval}


def run_station_trace_review(arguments, request_directory):
    return station_trace_review(*launchpad_inputs(arguments, request_directory), arguments["sourceStation"],
                                request_directory / arguments["trace"], expected_motion=expected_release_motion(arguments),
                                position_tolerance_um=arguments.get("positionToleranceMicrometres"),
                                waypoint_tolerance_um=arguments.get("waypointToleranceMicrometres"),
                                **sampled_speed_options(arguments))


def run_cycle_trace_review(arguments, request_directory):
    status_report = arguments.get("hmiStatusReport")
    environment_report = arguments.get("hmiEnvironmentReport")
    if environment_report and not status_report:
        raise InvalidRequest("hmiEnvironmentReport needs hmiStatusReport")
    scan_csv = arguments.get("scanCsv")
    release_total_event = arguments.get("releaseTotalEvent")
    if release_total_event is not None and release_total_event not in RELEASE_TOTAL_EVENTS:
        raise InvalidRequest(f"releaseTotalEvent must be one of {', '.join(RELEASE_TOTAL_EVENTS)}")
    require_capture = arguments.get("requireSessionScanCapture", False)
    if require_capture and not status_report:
        raise InvalidRequest("requireSessionScanCapture needs hmiStatusReport")
    require_hold = arguments.get("requireHoldThroughReopen", False)
    if require_hold and not status_report:
        raise InvalidRequest("requireHoldThroughReopen needs hmiStatusReport")
    require_widget = arguments.get("requireHmiAssemblyWidget", False)
    if require_widget and not status_report:
        raise InvalidRequest("requireHmiAssemblyWidget needs hmiStatusReport")
    layout_report = arguments.get("hmiLayoutSvgReport")
    if layout_report and not status_report:
        raise InvalidRequest("hmiLayoutSvgReport needs hmiStatusReport")
    verify_layout = arguments.get("verifyCapturedLayout", False)
    if verify_layout and (not status_report or layout_report):
        raise InvalidRequest("verifyCapturedLayout needs hmiStatusReport and cannot be used with hmiLayoutSvgReport")
    layout_checks = {
        "verify_mapping_line_endpoints": arguments.get("verifyMappingLineEndpoints", False),
        "verify_traffic_labels": arguments.get("verifyTrafficLabels", False),
    }
    if not verify_layout and any(layout_checks.values()):
        raise InvalidRequest("verifyMappingLineEndpoints and verifyTrafficLabels need verifyCapturedLayout in a cycle request")
    verify_time_handoff = arguments.get("verifyPositionTimeHandoff", False)
    if verify_time_handoff and not arguments.get("verifySampledSpeed", False):
        raise InvalidRequest("verifyPositionTimeHandoff requires verifySampledSpeed to be true")
    verify_scan_clock = arguments.get("verifyScanClock", False)
    max_release_to_next_hold_us = arguments.get("maxReleaseToNextHoldMicroseconds")
    if max_release_to_next_hold_us is not None and not verify_scan_clock:
        raise InvalidRequest("maxReleaseToNextHoldMicroseconds needs verifyScanClock")
    max_position_age = arguments.get("maxPositionSampleAgeMicroseconds")
    outbound_tolerance = arguments.get("positionToleranceMicrometres")
    return_tolerance = arguments.get("returnPositionToleranceMicrometres")
    waypoint_tolerance = arguments.get("waypointToleranceMicrometres")
    check_position = arguments.get("verifySampledSpeed", False) or any(
        value is not None for value in (outbound_tolerance, return_tolerance, waypoint_tolerance)
    )
    if max_position_age is not None and not (verify_scan_clock and check_position):
        raise InvalidRequest("maxPositionSampleAgeMicroseconds needs verifyScanClock and a station position, waypoint, or sampled speed check")
    return cycle_trace_review(*launchpad_inputs(arguments, request_directory), arguments["sourceStation"],
                              request_directory / arguments["trace"],
                              hmi_status_path=request_directory / status_report if status_report else None,
                              hmi_environment_report_path=request_directory / environment_report if environment_report else None,
                              scan_csv_path=request_directory / scan_csv if scan_csv else None,
                              require_session_capture=require_capture, expected_motion=expected_release_motion(arguments),
                              position_tolerance_um=outbound_tolerance,
                              return_position_tolerance_um=return_tolerance,
                              waypoint_tolerance_um=waypoint_tolerance,
                              verify_position_time_handoff=verify_time_handoff,
                              require_hold_through_reopen=require_hold,
                              require_assembly_widget=require_widget,
                              hmi_layout_report_path=request_directory / layout_report if layout_report else None,
                              captured_layout_checks=layout_checks if verify_layout else None,
                              release_total_event=release_total_event,
                              max_release_to_next_hold_scans=arguments.get("maxReleaseToNextHoldScans"),
                              max_release_to_next_hold_us=max_release_to_next_hold_us,
                              verify_scan_clock=verify_scan_clock, max_position_sample_age_us=max_position_age,
                              **sampled_speed_options(arguments))


def run_launchpad_motion_review(arguments, request_directory):
    return launchpad_route_review(*launchpad_inputs(arguments, request_directory), include_motion=True)


def run_launchpad_recovery_review(arguments, request_directory):
    return launchpad_route_review(*launchpad_inputs(arguments, request_directory), include_recovery=True)


def run_launchpad_geometry_review(arguments, request_directory):
    orientation = arguments.get("expectedHandedness")
    if orientation is not None and orientation not in {"preserved", "reversed"}:
        raise InvalidRequest("expectedHandedness must be preserved or reversed")
    criteria = GeometryCriteria(arguments.get("distanceToleranceMicrometres"), orientation)
    return run_launchpad_route_review(arguments, request_directory, criteria)


def run_launchpad_layout_review(arguments, request_directory):
    return run_launchpad_route_review(arguments, request_directory, include_layout=True)


def run_launchpad_frame_review(arguments, request_directory):
    datum = arguments.get("datumFile")
    return launchpad_frame_review(*launchpad_inputs(arguments, request_directory),
                                  datum_path=request_directory / datum if datum is not None else None,
                                  tolerance_um=arguments.get("positionToleranceMicrometres"))


def binding_target(arguments):
    return BindingTarget(arguments["visualizationId"], arguments["contentId"],
                         arguments["widgetId"], arguments["stringIdentifier"],
                         require_default=arguments.get("requireDefaultVisualization", False),
                         verify_iec_type=arguments.get("verifyIecType", False))


def saved_binding_review(arguments, request_directory):
    target = binding_target({**arguments, "verifyIecType": True})
    return review_saved_binding(project_path(arguments, request_directory), arguments.get("configuration"),
                                arguments.get("cpu"), target,
                                require_start_page=arguments.get("requireStartPage", False))


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
    start_widget = StartPageWidget(arguments.get("assemblyWidgetId")) if arguments.get("requireStartPage") else None
    return arsim_smoke(*arsim_inputs(arguments, request_directory), runtime_probe,
                       require_widget_bundle=arguments.get("requireWidgetBundle", False), start_widget=start_widget)


def run_arsim_browser_smoke(arguments, request_directory):
    probe = ChromeProbe(request_directory / arguments["browserExecutable"], arguments["counterWidgetId"],
                        arguments.get("assemblyWidgetId"), arguments.get("browserTimeoutSeconds", 30),
                        require_layout=arguments.get("requireLayoutAssets", False),
                        capture_layout=arguments.get("captureLayoutAssets", False),
                        require_widget_bundle=arguments.get("requireWidgetBundle", False))
    return run_arsim_smoke(arguments, request_directory, probe)


def run_arsim_opcua_smoke(arguments, request_directory):
    probe = OpcUaProbe(arguments["namespaceUri"], arguments["stringIdentifier"],
                       arguments.get("opcUaTimeoutSeconds", 20))
    return run_arsim_smoke(arguments, request_directory, probe)


def run_arsim_hmi_smoke(arguments, request_directory, status_bindings=(), scan_capture=None):
    return arsim_hmi_smoke(
        *arsim_inputs(arguments, request_directory),
        binding_target(arguments), request_directory / arguments["browserExecutable"],
        arguments.get("assemblyWidgetId"), arguments.get("browserTimeoutSeconds", 30),
        arguments.get("opcUaTimeoutSeconds", 20), require_layout=arguments.get("requireLayoutAssets", False),
        capture_layout=arguments.get("captureLayoutAssets", False),
        require_widget_bundle=arguments.get("requireWidgetBundle", False),
        require_start_page=arguments.get("requireStartPage", False),
        status_bindings=status_bindings, scan_capture=scan_capture,
    )


def run_arsim_status_smoke(arguments, request_directory):
    filename = arguments.get("scanCaptureFile")
    if filename is None and "scanCaptureTimeoutSeconds" in arguments:
        raise InvalidRequest("scanCaptureTimeoutSeconds needs scanCaptureFile")
    capture = RuntimeScanCapture(filename, arguments.get("scanCaptureTimeoutSeconds", 20)) if filename else None
    bindings = read_status_bindings(request_directory / arguments["statusBindingFile"], binding_target(arguments))
    return run_arsim_hmi_smoke(arguments, request_directory, bindings, capture)


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
WIDGET_PACKAGE_REVIEW = Operation(
    name="widget_package_review",
    summary="Compare saved mapp View widget library registrations with immediate directory entries.",
    arguments={
        "project": PROJECT_INSPECT.arguments["project"],
        "jobDirectory": {"type": "string", "minLength": 1, "description": "Optional retained native build, log and RUC."},
    },
    required=("project",),
    handler=run_widget_package_review,
)
BUILD_PREFLIGHT_INPUTS = {
    **PROJECT_INSPECT.arguments,
    "studioRoot": {"type": "string", "minLength": 1, "description": "Automation Studio installation root."},
    "verifyMotionDeclarations": {"type": "boolean", "default": False,
                                 "description": "Verify descriptor-listed IEC declarations for selected McAcp6D and MpTraffic."},
    "runtimeRoot": {"type": "string", "minLength": 1, "description": "Override the AS4 Runtime catalog under AS/System."},
}
BUILD_PREFLIGHT = Operation(
    name="build_preflight",
    summary="Compare a saved project with a selected AS4 installation and Runtime catalog.",
    arguments={
        **BUILD_PREFLIGHT_INPUTS,
        "expectedControllerType": {"type": "string", "minLength": 1,
                                   "description": "Require this Hardware.hw Module Type and its exact installed hardware revisions."},
        "controllerIoAssembly": {"type": "string", "minLength": 1,
                                 "description": "Check this assembly's saved 200-word input and output mapping. Requires expectedControllerType."},
        "controllerIoTaskClass": {"type": "integer", "minimum": 1, "maximum": 8,
                                  "description": "Require this Cyclic class on all communication rows and read its saved CPU timing. Requires controllerIoAssembly."},
        "controllerPowerlinkConnector": {"type": "string", "minLength": 1,
                                         "description": "Check a direct 6D connection, system timer and saved cycle time on this CPU connector. Requires expectedControllerType."},
        "controllerPowerlinkAssembly": {"type": "string", "minLength": 1,
                                        "description": "Compare this assembly's saved interface and node number with the direct connection. Requires controllerPowerlinkConnector."},
        "physicalAssembly": {"type": "string", "minLength": 1,
                             "description": "Check that this selected 6D assembly and CPU have simulation disabled for physical handoff."},
        "mappViewEthernetConnector": {"type": "string", "minLength": 1,
                                      "description": "Review the saved static IPv4 and mapp View startup on this dedicated CPU Ethernet connector."},
    },
    required=("project", "studioRoot"),
    handler=preflight,
)
MOTION_INTERFACE_REVIEW = Operation(
    name="motion_interface_review",
    summary="Read public pins, STRUCT fields and enum values from verified AS4 McAcp6D and MpTraffic declarations.",
    arguments={
        **{key: value for key, value in BUILD_PREFLIGHT_INPUTS.items() if key != "verifyMotionDeclarations"},
        "blocks": {"type": "array", "minItems": 1, "maxItems": 32,
                   "items": {"type": "string", "minLength": 1, "maxLength": 128},
                   "description": "Distinct IEC function-block names in McAcp6D or MpTraffic."},
        "enumTypes": {"type": "array", "minItems": 1, "maxItems": 32,
                       "items": {"type": "string", "minLength": 1, "maxLength": 128},
                       "description": "Selected IEC enumeration types with explicit decimal members."},
        "fieldPaths": {"type": "array", "minItems": 1, "maxItems": 64,
                       "items": {"type": "string", "minLength": 1, "maxLength": 512},
                       "description": "Optional Block.Pin.Field paths through directly declared STRUCT members."},
    },
    required=BUILD_PREFLIGHT.required,
    handler=run_motion_interface_review,
)
SIMULATION_ARGUMENTS = {
    "studioRoot": BUILD_PREFLIGHT_INPUTS["studioRoot"],
    "targetMapp6D": {"type": "string", "minLength": 3, "description": "Reviewed AS4 family, 2.4 or 2.7."},
    "simulatorZip": {"type": "string", "minLength": 1, "description": "Optional B&R simulator ZIP to match by SHA-256."},
    **{role: {"type": "string", "minLength": 1,
              "description": f"Optional {reference['release']} ZIP to compare by SHA-256."}
       for role, reference in COMPANION_MEDIA_27.items()},
}
SIMULATION_ENVIRONMENT = Operation(
    name="simulation_environment",
    summary="Check installed AS4 packages, a proposed package selection, and simulator media and registration.",
    arguments={
        **SIMULATION_ARGUMENTS,
        **{f"{name}Selector": {"type": "string", "minLength": 3,
                               "description": f"Exact proposed {name} selector. Supply all three selectors together."}
           for name in SIMULATION_PACKAGES},
    },
    required=("studioRoot", "targetMapp6D"),
    handler=run_simulation_environment,
)
SIMULATION_PREFLIGHT = Operation(
    name="simulation_preflight",
    summary="Check a saved AS4 project, workstation and native export prerequisites for matched ACOPOS 6D simulation.",
    arguments={
        **BUILD_PREFLIGHT_INPUTS,
        **SIMULATION_ARGUMENTS,
        "launchPadExport": {"type": "string", "minLength": 1, "description": "Native LaunchPad .x6d for the intended route."},
        "environmentReport": {"type": "string", "minLength": 1,
                              "description": "Retained simulation_environment JSON with a verified three-package selection."},
        "inspectLaunchPadExport": {"type": "boolean", "default": False,
                                  "description": "Inventory XML groups observed in the AS4 2.2 LaunchPad importer."},
        "verifyMpTrafficEnum": {"type": "boolean", "default": False,
                                "description": "Compare saved MpTraffic detection values and IA32 binary with the installed selection."},
    },
    required=(*BUILD_PREFLIGHT.required, "targetMapp6D"),
    handler=run_simulation_preflight,
)
BUILD_PLAN = Operation(
    name="build_plan",
    summary="Pin an offline AS4 PC_any simulation build to the source and installation read sets.",
    arguments={
        **BUILD_PREFLIGHT_INPUTS,
        **LAUNCHPAD_ROUTE_INPUTS,
        "launchPadExport": SIMULATION_PREFLIGHT.arguments["launchPadExport"],
        "environmentReport": SIMULATION_PREFLIGHT.arguments["environmentReport"],
        "verifyLaunchPadLayout": {"type": "boolean", "default": False,
                                  "description": "Verify and pin the linked 240 mm S3-AS LaunchPad segment grid."},
        "verifyLaunchPadPositions": {"type": "boolean", "default": False,
                                     "description": "Pin the saved absolute station and waypoint XY values for RUC review."},
        "launchPadWidgetId": {"type": "string", "minLength": 1,
                              "description": "Pin the default start page's Assembly6D DOM ID and require it to show the reviewed manager."},
        "verifyLaunchPadRules": {"type": "boolean", "default": False,
                                 "description": "Pin the selected manager's linked rule lists and typed exclusivity roles."},
        "verifyLaunchPadRecovery": {"type": "boolean", "default": False,
                                    "description": "Verify and pin the reviewed station eligibility, mode and indexed recovery points."},
        "verifyLaunchPadMotionLimits": {"type": "boolean", "default": False,
                                        "description": "Pin the linked assembly's explicit in-plane limits and the raw LaunchPad exit values."},
        "launchPadReadAxes": {"type": "array", "minItems": 1, "maxItems": len(READ_AXES), "uniqueItems": True,
                              "items": {"type": "string", "minLength": 1, "maxLength": 2, "enum": list(READ_AXES)},
                              "description": "Require these read axes and pin the linked assembly's I/O declarations and explicit flags."},
        "verifyMpTrafficEnum": SIMULATION_PREFLIGHT.arguments["verifyMpTrafficEnum"],
        "verifyMotionPayloads": {"type": "boolean", "default": False,
                                 "description": "Pin the six AS4 SG4/IA32 motion payloads and verify the produced RUC."},
        "verifyMotionSymbols": {"type": "boolean", "default": False,
                                "description": "Verify the generated IEC globals for the 6D assembly and Traffic Manager."},
    },
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
RUC_INPUTS = {
    "project": PROJECT_INSPECT.arguments["project"],
    "jobDirectory": {"type": "string", "minLength": 1, "description": "Directory holding the retained build receipts and RUC archive."},
}

RUC_REVIEW = Operation(
    name="ruc_review",
    summary="Verify a retained AS4 RUC job and compare its 6D modules, routes and widget links with saved sources.",
    arguments={
        **RUC_INPUTS,
        "startPageWidgetId": {"type": "string", "minLength": 1,
                              "description": "Optional Assembly6D DOM ID to verify on the default visualization's start page."},
    },
    required=("project", "jobDirectory"),
    handler=ruc_review,
)
ASSEMBLY_IO_REVIEW = Operation(
    name="assembly_io_review",
    summary="Trace AS4 assembly I/O references to saved global declarations and verify compiled read-axis flags.",
    arguments={
        **PROJECT_INSPECT.arguments,
        "assemblyId": {"type": "string", "minLength": 1, "description": "Selected assembly6d element ID."},
        "jobDirectory": RUC_REVIEW.arguments["jobDirectory"],
    },
    required=("project", "assemblyId"),
    handler=run_assembly_io_review,
)
SAVED_ASSEMBLY_WIDGET_REVIEW = Operation(
    name="saved_assembly_widget_review",
    summary="Verify the saved Assembly6D widget's startup placement, assembly and Traffic Manager.",
    arguments={
        **PROJECT_INSPECT.arguments,
        "startPageWidgetId": RUC_REVIEW.arguments["startPageWidgetId"],
        "assemblyId": ASSEMBLY_IO_REVIEW.arguments["assemblyId"],
        "managerId": {"type": "string", "minLength": 1, "description": "Expected saved Traffic Manager element ID."},
    },
    required=("project", "assemblyId", "managerId"),
    handler=saved_assembly_widget_review,
)
LAYOUT_DEVICE_REVIEW = Operation(
    name="layout_device_review",
    summary="Verify the saved and packaged AS4 CPU file device used by the Assembly6D layout.",
    arguments={**PROJECT_INSPECT.arguments, "jobDirectory": RUC_REVIEW.arguments["jobDirectory"]},
    required=("project",),
    handler=run_layout_device_review,
)
LAYOUT_SVG_REVIEW = Operation(
    name="layout_svg_review",
    summary="Compare retained AS4 layout SVG geometry and traffic links with the saved project and RUC.",
    arguments={
        **RUC_INPUTS,
        "managerId": {"type": "string", "minLength": 1, "description": "Selected Traffic Manager element ID."},
        "assemblySvg": {"type": "string", "minLength": 1, "description": "Retained assembly SVG file. Supply with trafficSvg for explicit-file review."},
        "trafficSvg": {"type": "string", "minLength": 1, "description": "Retained Traffic Manager SVG file. Supply with assemblySvg for explicit-file review."},
        "hmiStatusReport": {"type": "string", "minLength": 1,
                            "description": "Completed arsim_status_smoke JSON beside layout-assets/. Supply in place of assemblySvg and trafficSvg."},
        "verifyMappingLineEndpoints": {"type": "boolean", "default": False,
                                       "description": "Verify the drawn start and end coordinates of each generated one-way mapping."},
        "verifyTrafficLabels": {"type": "boolean", "default": False,
                                "description": "Verify the text and anchor of each generated station and waypoint label."},
    },
    required=("project", "jobDirectory", "managerId"),
    handler=run_layout_svg_review,
)
LAUNCHPAD_ROUTE_REVIEW = Operation(
    name="launchpad_route_review",
    summary="Compare one saved LaunchPad cycle with the typed route of a selected saved or compiled Traffic Manager.",
    arguments={
        **PROJECT_INSPECT.arguments,
        **LAUNCHPAD_ROUTE_INPUTS,
        "jobDirectory": RUC_REVIEW.arguments["jobDirectory"],
    },
    required=("project", "managerId", "launchPadProject", "launchPadReadback"),
    handler=run_launchpad_route_review,
)
LAUNCHPAD_EXPORT_REVIEW = Operation(
    name="launchpad_export_review",
    summary="Compare the saved LaunchPad cycle with the typed route in an observed AS4 XML export.",
    arguments={
        "launchPadProject": LAUNCHPAD_ROUTE_REVIEW.arguments["launchPadProject"],
        "launchPadReadback": LAUNCHPAD_ROUTE_REVIEW.arguments["launchPadReadback"],
        "launchPadExport": SIMULATION_PREFLIGHT.arguments["launchPadExport"],
    },
    required=("launchPadProject", "launchPadReadback", "launchPadExport"),
    handler=run_launchpad_export_review,
)
LAUNCHPAD_STATION_REVIEW = Operation(
    name="launchpad_station_review",
    summary="Read station-to-station paths and LaunchPad model waits for a matched saved or compiled Traffic Manager route.",
    arguments=LAUNCHPAD_ROUTE_REVIEW.arguments,
    required=LAUNCHPAD_ROUTE_REVIEW.required,
    handler=run_launchpad_station_review,
)
STATION_TRACE_INPUTS = {
    **LAUNCHPAD_ROUTE_REVIEW.arguments,
    "sourceStation": {"type": "string", "minLength": 1, "description": "Imported name of the inspected source station."},
    "trace": {"type": "string", "minLength": 1, "description": "File containing the normalized scan trace."},
    "expectedExitVelocityMps": {"type": "string", "minLength": 1,
                                "description": "Expected ExitVelocity on the Release edge as a decimal string in m/s."},
    "expectedExitAccelerationMps2": {"type": "string", "minLength": 1,
                                     "description": "Expected ExitAcceleration on the Release edge as a decimal string in m/s²."},
    "positionToleranceMicrometres": {"type": "integer", "minimum": 0, "maximum": 9007199254740991,
                                      "description": "Maximum per-axis difference from the compiled station position at Release and arrival."},
    "waypointToleranceMicrometres": {"type": "integer", "minimum": 0, "maximum": 9007199254740991,
                                      "description": "Maximum per-axis difference for ordered waypoint position observations."},
    "verifySampledSpeed": {"type": "boolean", "default": False,
                           "description": "Compare sampled XY displacement rates with the compiled in-plane velocity limit."},
    "maxSampleIntervalMicroseconds": {"type": "integer", "minimum": 1, "maximum": 9007199254740991,
                                      "description": "Maximum elapsed time between usable XY samples. Requires verifySampledSpeed."},
}
STATION_TRACE_REVIEW = Operation(
    name="station_trace_review",
    summary="Compare saved inspection release and arrival evidence with a verified compiled station leg.",
    arguments=STATION_TRACE_INPUTS,
    required=(*LAUNCHPAD_ROUTE_REVIEW.required, "jobDirectory", "sourceStation", "trace"),
    handler=run_station_trace_review,
)
CYCLE_TRACE_REVIEW = Operation(
    name="cycle_trace_review",
    summary="Check a recorded two-station inspection cycle including return and the next held request.",
    arguments={
        **STATION_TRACE_INPUTS,
        "trace": {"type": "string", "minLength": 1,
                  "description": "Normalized cycle trace or cycle trace descriptor for CSV samples."},
        "scanCsv": {"type": "string", "minLength": 1,
                    "description": "Recorded PLC scans. Strict review defaults to scan-capture.csv beside hmiStatusReport."},
        "hmiStatusReport": {"type": "string", "minLength": 1,
                            "description": "Completed arsim_status_smoke JSON for the same cycle RUC and session."},
        "hmiEnvironmentReport": {"type": "string", "minLength": 1,
                                 "description": "Require matching bytes from this retained workstation report. Requires hmiStatusReport."},
        "requireSessionScanCapture": {"type": "boolean", "default": False,
                                      "description": "Require the session's retained scan and completed HMI status receipt. Use the receipt for missing scan or HMI identities and the matched pinned build for missing inspection hashes."},
        "requireHoldThroughReopen": {"type": "boolean", "default": False,
                                     "description": "Require consecutive held PLC scans reaching the last direct heartbeat after HMI reopen. Requires hmiStatusReport."},
        "requireHmiAssemblyWidget": {"type": "boolean", "default": False,
                                     "description": "Require the cycle's Assembly6D widget and both layout SVGs in both HMI documents."},
        "hmiLayoutSvgReport": {"type": "string", "minLength": 1,
                               "description": "Completed layout_svg_review JSON for both SVGs captured by the HMI status probe."},
        "verifyCapturedLayout": {"type": "boolean", "default": False,
                                 "description": "Verify both SVGs beside hmiStatusReport in the cycle review. Use in place of hmiLayoutSvgReport."},
        "verifyMappingLineEndpoints": LAYOUT_SVG_REVIEW.arguments["verifyMappingLineEndpoints"],
        "verifyTrafficLabels": LAYOUT_SVG_REVIEW.arguments["verifyTrafficLabels"],
        "releaseTotalEvent": {"type": "string", "minLength": 1, "enum": list(RELEASE_TOTAL_EVENTS),
                              "description": "Require the sampled release total to increase once at the selected event scan and stay steady through the next hold."},
        "maxReleaseToNextHoldScans": {"type": "integer", "minimum": 1, "maximum": 9007199254740991,
                                      "description": "Maximum allowed scan index difference from the Release edge to the first next hold."},
        "maxReleaseToNextHoldMicroseconds": {"type": "integer", "minimum": 1, "maximum": 9007199254740991,
                                             "description": "Maximum measured time from the Release edge to the first next hold. Requires verifyScanClock."},
        "verifyScanClock": {"type": "boolean", "default": False,
                            "description": "Measure cycle intervals from one recorded microsecond clock spanning both legs and the next hold."},
        "maxPositionSampleAgeMicroseconds": {"type": "integer", "minimum": 0, "maximum": 9007199254740991,
                                             "description": "Maximum time between XY acquisition and the PLC scan record. Requires verifyScanClock and a station position, waypoint, or sampled speed check."},
        "verifyPositionTimeHandoff": {"type": "boolean", "default": False,
                                      "description": "Compare outbound arrival and return occupancy timestamps using a common clock. Requires verifySampledSpeed."},
        "returnPositionToleranceMicrometres": {"type": "integer", "minimum": 0, "maximum": 9007199254740991,
                                               "description": "Maximum per-axis position difference at return source, arrival and next hold."},
    },
    required=STATION_TRACE_REVIEW.required,
    handler=run_cycle_trace_review,
)
LAUNCHPAD_RECOVERY_REVIEW = Operation(
    name="launchpad_recovery_review",
    summary="Compare station recovery declarations and saved-to-compiled recovery point positions for a matched route.",
    arguments=LAUNCHPAD_ROUTE_REVIEW.arguments,
    required=LAUNCHPAD_ROUTE_REVIEW.required,
    handler=run_launchpad_recovery_review,
)
LAUNCHPAD_MOTION_REVIEW = Operation(
    name="launchpad_motion_review",
    summary="Read LaunchPad exit values and compare the linked assembly's saved and compiled in-plane limits.",
    arguments=LAUNCHPAD_ROUTE_REVIEW.arguments,
    required=LAUNCHPAD_ROUTE_REVIEW.required,
    handler=run_launchpad_motion_review,
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
LAUNCHPAD_LAYOUT_REVIEW = Operation(
    name="launchpad_layout_review",
    summary="Compare the selected manager's linked 240 mm S3 segment layout with the saved LaunchPad layout.",
    arguments=LAUNCHPAD_ROUTE_REVIEW.arguments,
    required=LAUNCHPAD_ROUTE_REVIEW.required,
    handler=run_launchpad_layout_review,
)
LAUNCHPAD_FRAME_REVIEW = Operation(
    name="launchpad_frame_review",
    summary="Compare station offsets under the same axes that align the selected LaunchPad and AS4 segment grids.",
    arguments={
        **LAUNCHPAD_ROUTE_REVIEW.arguments,
        "datumFile": {"type": "string", "minLength": 1, "description": "Optional hash-bound common point in both coordinate frames."},
        "positionToleranceMicrometres": {"type": "integer", "minimum": 0, "maximum": 9007199254740991,
                                        "description": "Optional maximum object residual for one reported transform."},
    },
    required=LAUNCHPAD_ROUTE_REVIEW.required,
    handler=run_launchpad_frame_review,
)
BINDING_SELECTION = {
    "visualizationId": {"type": "string", "minLength": 1, "description": "Saved visualization ID."},
    "contentId": {"type": "string", "minLength": 1, "description": "Saved content ID."},
    "widgetId": {"type": "string", "minLength": 1, "description": "Saved widget ID within that content."},
    "stringIdentifier": {"type": "string", "minLength": 1, "description": "Expected V1 OPC UA ::Task:Variable identifier."},
}
SAVED_BINDING_REVIEW = Operation(
    name="saved_binding_review",
    summary="Follow a saved one-way mapp View value binding to a local CPU OPC UA endpoint and IEC task scalar.",
    arguments={
        **PROJECT_INSPECT.arguments,
        **BINDING_SELECTION,
        "requireDefaultVisualization": {"type": "boolean", "default": False,
                                         "description": "Require the selected visualization to match the saved mapp View default."},
        "requireStartPage": {"type": "boolean", "default": False,
                             "description": "Require the selected content to be assigned directly to an area on the default visualization's saved start page."},
    },
    required=("project", *BINDING_SELECTION),
    handler=saved_binding_review,
)
RUC_BINDING_REVIEW = Operation(
    name="ruc_binding_review",
    summary="Trace a one-way mapp View value binding from saved sources through a reviewed AS4 RUC.",
    arguments={
        **RUC_INPUTS,
        **BINDING_SELECTION,
        "verifyIecType": {"type": "boolean", "default": False,
                          "description": "Match the compiled OPC UA data type to the selected IEC task declaration."},
    },
    required=(*RUC_REVIEW.required, *BINDING_SELECTION),
    handler=ruc_binding_review,
)
ARSIM_SMOKE = Operation(
    name="arsim_smoke",
    summary="Materialize a reviewed AS4 RUC and verify its mapp View assets over owned loopback HTTP.",
    arguments={
        **RUC_INPUTS,
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
        "requireStartPage": {"type": "boolean", "default": False,
                             "description": "Verify the Assembly6D startup route and require the generated client map in both browser documents."},
        "requireWidgetBundle": {"type": "boolean", "default": False,
                                "description": "Check the packaged Assembly6D bundle and require its script load in both HMI documents."},
        "requireLayoutAssets": {"type": "boolean", "default": False,
                                "description": "Require expected 6DData requests with HTTP 200 or 304 and a bounded inline SVG in each HMI document."},
        "captureLayoutAssets": {"type": "boolean", "default": False,
                                "description": "Require browser layout evidence and save the expected SVGs from the owned ARsim HTTP listener."},
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
        "requireStartPage": ARSIM_BROWSER_SMOKE.arguments["requireStartPage"],
        "requireWidgetBundle": ARSIM_BROWSER_SMOKE.arguments["requireWidgetBundle"],
        "requireLayoutAssets": ARSIM_BROWSER_SMOKE.arguments["requireLayoutAssets"],
        "captureLayoutAssets": ARSIM_BROWSER_SMOKE.arguments["captureLayoutAssets"],
        "opcUaTimeoutSeconds": ARSIM_OPCUA_SMOKE.arguments["opcUaTimeoutSeconds"],
    },
    required=(*ARSIM_SMOKE.required, "visualizationId", "contentId", "widgetId", "stringIdentifier", "browserExecutable"),
    handler=run_arsim_hmi_smoke,
)
ARSIM_STATUS_SMOKE = Operation(
    name="arsim_status_smoke",
    summary="Verify read-only numeric HMI status and an advancing counter before and after reopening.",
    arguments={
        **ARSIM_HMI_SMOKE.arguments,
        "statusBindingFile": {"type": "string", "minLength": 1, "description": "JSON file with expected numeric widget bindings."},
        "scanCaptureFile": {"type": "string", "minLength": 1,
                            "description": "Finalized .csv filename in the owned ARsim USER directory."},
        "scanCaptureTimeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 120, "default": 20},
    },
    required=(*ARSIM_HMI_SMOKE.required, "statusBindingFile"),
    handler=run_arsim_status_smoke,
)
OPERATIONS = {item.name: item for item in (
    PROJECT_INSPECT, WIDGET_PACKAGE_REVIEW, BUILD_PREFLIGHT, MOTION_INTERFACE_REVIEW, SIMULATION_ENVIRONMENT, SIMULATION_PREFLIGHT, BUILD_PLAN, BUILD_EXECUTE, RUC_REVIEW,
    ASSEMBLY_IO_REVIEW, SAVED_BINDING_REVIEW, SAVED_ASSEMBLY_WIDGET_REVIEW, LAYOUT_DEVICE_REVIEW, LAYOUT_SVG_REVIEW,
    LAUNCHPAD_ROUTE_REVIEW, LAUNCHPAD_EXPORT_REVIEW, LAUNCHPAD_STATION_REVIEW, STATION_TRACE_REVIEW, CYCLE_TRACE_REVIEW,
    LAUNCHPAD_RECOVERY_REVIEW, LAUNCHPAD_MOTION_REVIEW,
    LAUNCHPAD_GEOMETRY_REVIEW, LAUNCHPAD_LAYOUT_REVIEW, LAUNCHPAD_FRAME_REVIEW, RUC_BINDING_REVIEW,
    ARSIM_SMOKE, ARSIM_BROWSER_SMOKE, ARSIM_OPCUA_SMOKE, ARSIM_HMI_SMOKE, ARSIM_STATUS_SMOKE,
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
        elif schema["type"] == "boolean":
            if type(value) is not bool:
                raise InvalidRequest(f"{key} must be a Boolean")
        elif schema["type"] == "array":
            item = schema["items"]
            if (not isinstance(value, list) or not schema["minItems"] <= len(value) <= schema["maxItems"]
                    or any(not valid_text(entry) or not item["minLength"] <= len(entry) <= item["maxLength"]
                           for entry in value)):
                raise InvalidRequest(f"{key} must be an array of {schema['minItems']} to {schema['maxItems']} strings")
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
