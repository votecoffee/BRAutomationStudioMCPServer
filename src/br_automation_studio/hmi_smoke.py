"""Check a saved status binding, direct node and browser in one owned ARsim session."""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import ClassVar

from .arsim_smoke import HOST, arsim_smoke
from .browser_smoke import ChromeProbe
from .opcua_smoke import INTEGER_NODE_TYPES, OpcUaProbe
from .ruc_review import review_ruc


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
class HmiProbe:
    reviewed_binding: dict
    browser: ChromeProbe
    opcua_timeout: int
    key: ClassVar[str] = "hmi"
    evidence: ClassVar[str] = "arsim_loopback_hmi"

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
        opcua = OpcUaProbe(endpoint["namespaceUri"], binding["stringIdentifier"], self.opcua_timeout)
        receipt["bindingReview"] = binding
        before = receipt["opcUaBefore"] = {}
        ua_before = opcua.prepare(reviewed, profile, before)
        browser = receipt["browser"] = {}
        read_browser = self.browser.prepare(reviewed, profile, browser)
        after = receipt["opcUaAfter"] = {}
        ua_after = opcua.prepare(reviewed, profile, after)
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
            receipt["state"] = "complete"

        return probe


def arsim_hmi_smoke(project, job_dir, pvi, session_dir, timeout, target, chrome,
                    assembly_widget, browser_timeout, opcua_timeout, *, require_layout=False):
    binding_report = review_ruc(project, job_dir, replace(target, require_default=True))
    if binding_report["evidence"] != "ruc_binding_readback":
        binding_report["arsimSmoke"] = {"state": "not_started", "packageSha256": None, "assets": [], "cleanup": None}
        return binding_report

    browser = ChromeProbe(chrome, target.dom_id, assembly_widget, browser_timeout, require_layout=require_layout)
    probe = HmiProbe(binding_report, browser, opcua_timeout)
    report = arsim_smoke(project, job_dir, pvi, session_dir, timeout, probe, extra_content=(target.content,))
    report["bindingReview"] = binding_report["bindingReview"]
    report["bindingSnapshot"] = binding_report["snapshot"]
    return report
