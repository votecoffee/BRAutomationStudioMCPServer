"""Check a saved status binding, direct node and browser in one owned ARsim session."""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import ClassVar

from .arsim_smoke import HOST, arsim_smoke
from .browser_smoke import ChromeProbe
from .json_io import read_json
from .opcua_smoke import INTEGER_NODE_TYPES, OpcUaProbe, StatusNode
from .ruc_review import review_ruc
from .start_page import StartPageWidget
from .runtime_capture import RuntimeScanCapture

HELD_STATUS_ROLES = ("requestId", "shuttleId", "phase", "releaseTotal")


def check_samples(before, browser, after, expected_type, comparison):
    if any(before[key] != after[key] for key in ("ownedListener", "nodeId")):
        raise ValueError("The direct reads did not use the same owned OPC UA listener and node")
    if before["variantType"] != expected_type or after["variantType"] != expected_type:
        raise ValueError("The direct OPC UA type differs from the compiled node type")
    strings = browser["initial"]["counterSamples"] + browser["reopened"]["counterSamples"]
    values = [Decimal(value) for value in strings]
    lower, upper = before["samples"][-1]["value"], after["samples"][0]["value"]
    comparison.update(directBeforeLast=lower, browserFirst=strings[0], browserLast=strings[-1],
                      directAfterFirst=upper, variantType=expected_type)
    if any(value != value.to_integral_value() or not lower <= value <= upper for value in values):
        raise ValueError("The displayed integer samples are outside the direct OPC UA readings around the browser run")


@dataclass(frozen=True)
class StatusBinding:
    widget: str
    identifier: str
    expected: int
    role: str | None = None


def read_status_bindings(path, counter):
    document = read_json(path, 1024 * 1024)
    if (not isinstance(document, dict) or type(document.get("schemaVersion")) is not int
            or document["schemaVersion"] != 1):
        raise ValueError("Expected a schemaVersion 1 status binding file")
    bindings = document.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("Supply a nonempty list of expected status bindings")
    widgets, identifiers = {counter.widget}, {counter.identifier}
    roles = set()
    fields = {"widgetId", "stringIdentifier", "expectedValue"}
    result = []
    for row in bindings:
        if not isinstance(row, dict) or not fields <= row.keys() <= fields | {"role"}:
            raise ValueError("Each status binding needs widgetId, stringIdentifier and expectedValue")
        widget, identifier, expected = (row[key] for key in ("widgetId", "stringIdentifier", "expectedValue"))
        if not all(isinstance(value, str) and value.strip() and "\0" not in value for value in (widget, identifier)):
            raise ValueError("Status widget and node names must be nonempty strings")
        if type(expected) is not int or abs(expected) > 2**53 - 1:
            raise ValueError("Expected a status integer within the browser's exact integer range")
        if widget in widgets or identifier in identifiers:
            raise ValueError("Select distinct status widgets and OPC UA identifiers, separate from the counter")
        role = row.get("role")
        if "role" in row:
            if role not in HELD_STATUS_ROLES or role in roles:
                raise ValueError(f"Each specified held-status role must be unique and one of {', '.join(HELD_STATUS_ROLES)}")
            roles.add(role)
        widgets.add(widget)
        identifiers.add(identifier)
        result.append(StatusBinding(widget, identifier, expected, role))
    return tuple(result)


@dataclass(frozen=True)
class HmiProbe:
    reviewed_binding: dict
    browser: ChromeProbe
    opcua_timeout: int
    status_bindings: tuple = ()
    scan_capture: RuntimeScanCapture | None = None
    key: ClassVar[str] = "hmi"

    @property
    def evidence(self):
        return "arsim_loopback_status" if self.status_bindings else "arsim_loopback_hmi"

    def prepare(self, reviewed, profile, receipt):
        original = self.reviewed_binding["rucReview"]
        if any(reviewed[key] != original[key] for key in ("jobId", "planSha256", "archive")):
            raise ValueError("The binding and 6D reviews do not identify the same completed build and RUC")
        binding = self.reviewed_binding["bindingReview"]
        compiled = binding["compiled"]
        endpoint = compiled["endpoint"]
        port = profile["opcUaPort"]
        if (endpoint["host"], endpoint["port"]) != (HOST, port):
            raise ValueError("The reviewed binding endpoint differs from the owned loopback OPC UA profile")
        expected = INTEGER_NODE_TYPES.get(compiled["dataType"])
        if expected is None:
            raise ValueError("The combined HMI check needs an integer counter in the compiled OPC UA node map")
        status_nodes = []
        for item in self.status_bindings:
            status = item["bindingReview"]
            compiled_status = status["compiled"]
            if item["archive"] != original["archive"] or compiled_status["endpoint"] != endpoint:
                raise ValueError("The status bindings must use the same completed RUC and local OPC UA endpoint")
            status_type = INTEGER_NODE_TYPES.get(compiled_status["dataType"])
            if status_type is None:
                raise ValueError("Each status binding needs an integer in the compiled OPC UA node map")
            status_nodes.append(StatusNode(status["stringIdentifier"], status_type, item["expectedValue"]))
        opcua = OpcUaProbe(endpoint["namespaceUri"], binding["stringIdentifier"], self.opcua_timeout, tuple(status_nodes))
        receipt["bindingReview"] = binding
        if self.status_bindings:
            receipt["statusBindingReviews"] = list(self.status_bindings)
        before = receipt["opcUaBefore"] = {}
        ua_before = replace(opcua, wait_for_status=bool(status_nodes)).prepare(reviewed, profile, before)
        browser = receipt["browser"] = {}
        read_browser = self.browser.prepare(reviewed, profile, browser)
        after = receipt["opcUaAfter"] = {}
        ua_after = opcua.prepare(reviewed, profile, after)
        if self.scan_capture is not None:
            receipt["scanCapture"] = {"state": "not_started"}
        receipt["state"] = "ready"

        def probe(session_dir, owned_listener):
            receipt["state"] = "opcua_before_browser"
            ua_before(session_dir, owned_listener)

            def original_opcua_listener(port_to_check):
                owner = owned_listener(port)
                if owner != before["ownedListener"]:
                    raise ValueError("The original OPC UA listener changed during the combined HMI check")
                return owner if port_to_check == port else owned_listener(port_to_check)

            receipt["state"] = "browser"
            read_browser(session_dir, original_opcua_listener)
            receipt["state"] = "opcua_after_browser"
            ua_after(session_dir, original_opcua_listener)
            receipt["state"] = "comparing_values"
            check_samples(before, browser, after, expected, receipt.setdefault("comparison", {}))
            if self.scan_capture is not None:
                receipt["state"] = "retaining_scan"
                self.scan_capture.retain(session_dir, lambda: original_opcua_listener(port), receipt["scanCapture"])
            receipt["state"] = "complete"

        return probe


def arsim_hmi_smoke(project, job_dir, pvi, session_dir, timeout, target, chrome,
                    assembly_widget, browser_timeout, opcua_timeout, *, require_layout=False, capture_layout=False,
                    require_widget_bundle=False, require_start_page=False, require_traffic_details=False,
                    require_traffic_labels=False, require_label_bounds=False, minimum_label_height=None, viewport=None,
                    status_bindings=(), scan_capture=None):
    binding_report = review_ruc(project, job_dir, replace(target, require_default=True))
    if binding_report["evidence"] != "ruc_binding_readback":
        binding_report["arsimSmoke"] = {"state": "not_started", "packageSha256": None, "assets": [], "cleanup": None}
        return binding_report

    statuses = []
    for binding in status_bindings:
        status_target = replace(target, widget=binding.widget, identifier=binding.identifier, require_default=True)
        status = review_ruc(project, job_dir, status_target)
        if status["evidence"] != "ruc_binding_readback":
            status["arsimSmoke"] = {"state": "not_started", "packageSha256": None, "assets": [], "cleanup": None}
            return status
        row = {"bindingReview": status["bindingReview"], "snapshot": status["snapshot"],
               "archive": status["rucReview"]["archive"], "expectedValue": binding.expected}
        if binding.role is not None:
            row["role"] = binding.role
        statuses.append(row)
    expected_status = tuple((item["bindingReview"]["domId"], item["expectedValue"]) for item in statuses)
    require_traffic_details = (require_traffic_details or require_traffic_labels or require_label_bounds
                               or minimum_label_height is not None)
    start_widget = None
    if require_start_page or require_traffic_details:
        start_widget = StartPageWidget(assembly_widget, require_traffic_details)
    check_assembly = (not statuses or assembly_widget is not None or require_layout
                      or capture_layout or require_widget_bundle or start_widget is not None)
    browser = ChromeProbe(chrome, target.dom_id, assembly_widget, browser_timeout,
                          require_layout=require_layout, capture_layout=capture_layout,
                          expected_status=expected_status, check_assembly=check_assembly,
                          require_widget_bundle=require_widget_bundle, require_traffic_labels=require_traffic_labels,
                          minimum_label_height=minimum_label_height, viewport=viewport,
                          require_label_bounds=require_label_bounds)
    probe = HmiProbe(binding_report, browser, opcua_timeout, tuple(statuses), scan_capture)
    before_start = scan_capture.require_absent if scan_capture is not None else None
    report = arsim_smoke(project, job_dir, pvi, session_dir, timeout, probe, extra_content=(target.content,),
                         require_6d=check_assembly, before_start=before_start,
                         require_widget_bundle=require_widget_bundle, start_widget=start_widget)
    report["bindingReview"] = binding_report["bindingReview"]
    report["bindingSnapshot"] = binding_report["snapshot"]
    return report
