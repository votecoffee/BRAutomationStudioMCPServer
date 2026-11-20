"""Compare the saved and retained AS4 configuration for the Assembly6D file device."""

from pathlib import Path

from .file_device import DEVICE_NAME
from .inspection import inspect_project
from .ruc_review import review_ruc


def layout_device_review(project, configuration, cpu, job_directory=None):
    if job_directory is None:
        report = inspect_project(project, configuration, cpu, include_layout_device=True)
        saved = report.pop("savedLayoutDevice", None)
        compiled = None
    else:
        report = review_ruc(project, job_directory, include_layout_device=True)
        retained = report["rucReview"]
        saved = retained.pop("savedLayoutDevice", None)
        compiled = retained.pop("compiledLayoutDevice", None)

    result = report["layoutDeviceReview"] = {"name": DEVICE_NAME, "state": "not_checked",
                                              "saved": saved, "compiled": compiled, "pathComparison": "not_checked"}
    issue_start = len(report["diagnostics"])

    def issue(code, where, message, severity="error"):
        report["diagnostics"].append({"scope": "layout_device", "code": code, "path": Path(where).as_posix(),
                                      "message": message, "severity": severity})

    if job_directory is not None:
        if not retained["sourceMatched"] or retained["archive"] is None:
            return report
        for value, key in ((configuration, "selectedConfiguration"), (cpu, "selectedCpu")):
            if value is not None and value.casefold() != report[key].casefold():
                issue("layout_device_selection_mismatch", project, "Requested configuration or CPU differs from the retained build")
                return report
    if saved is None:
        return report
    if saved["state"] == "absent":
        issue("layout_device_missing", saved["path"], "The selected CPU has no saved 6DData file-device declaration")
    elif saved["state"] == "unverified":
        issue("layout_device_unverified", saved["path"], saved["reason"], "warning")
    if compiled is not None:
        if compiled["state"] == "absent" and saved["state"] == "present":
            issue("layout_device_not_compiled", compiled["path"], "The declared 6DData device is absent from the compiled tree")
        elif compiled["state"] == "unverified":
            issue("layout_device_unverified", compiled["path"], compiled["reason"], "warning")
        elif saved["state"] == compiled["state"] == "present":
            if saved["target"] != "USER_PATH":
                result["pathComparison"] = "unverified"
                issue("layout_device_path_unverified", saved["path"], "Path equivalence is supported for USER_PATH", "warning")
            elif compiled["deviceParameter"] != "/DEVICE=USER_PATH":
                result["pathComparison"] = "mismatch"
                issue("layout_device_path_mismatch", compiled["path"], "Compiled 6DData parameter differs from the saved USER_PATH declaration")
            else:
                result["pathComparison"] = "matched"

    if job_directory is not None and compiled is None:
        result["state"] = "unverified"
        return report
    severities = {row["severity"] for row in report["diagnostics"][issue_start:]}
    result["state"] = "issues" if "error" in severities else "unverified" if severities else "read"
    if not report["diagnostics"]:
        report["evidence"] = "layout_device_compiled" if job_directory is not None else "layout_device_saved"
    return report
