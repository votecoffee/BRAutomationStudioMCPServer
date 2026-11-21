"""Compare a selected AS4 manager with a source-bound saved LaunchPad cycle."""

from pathlib import Path

from .inspection import inspect_project
from .launchpad_baseline import baseline, compare_cycle, json_snapshot, names_for, nonempty, read_launchpad_save, route_baseline
from .launchpad_geometry import measure_geometry
from .launchpad_layout import compare_layout, linked_layout
from .launchpad_motion import review_motion
from .launchpad_recovery import review_recovery
from .launchpad_stations import station_legs
from .ruc_review import review_ruc

def launchpad_route_review(project, configuration, cpu, manager_id, lpj, readback, name_map=None, job_dir=None,
                           *, geometry=None, include_layout=False, include_stations=False, include_recovery=False,
                           include_motion=False):
    geometry_manager = manager_id if geometry is not None else None
    recovery_manager = manager_id if include_recovery else None
    if job_dir is not None:
        report = review_ruc(project, job_dir, geometry_manager=geometry_manager, include_layout=include_layout,
                            recovery_manager=recovery_manager, include_motion=include_motion)
    else:
        report = inspect_project(project, configuration, cpu, geometry_manager=geometry_manager,
                                 include_layout=include_layout, recovery_managers=(recovery_manager,),
                                 include_motion=include_motion)
    route = report["routeReview"] = {"state": "not_checked", "managerId": manager_id,
                                     "view": "compiled" if job_dir is not None else "saved"}
    if geometry is not None:
        report["geometryReview"] = {"state": "not_checked"}
    if include_layout:
        report["layoutReview"] = {"state": "not_checked"}
    if include_stations:
        report["stationReview"] = {"state": "not_checked", "nativeRecipe": "not_checked", "legs": []}
    if include_recovery:
        report["recoveryReview"] = {"state": "not_checked", "stations": []}
    if include_motion:
        report["motionReview"] = {"state": "not_checked", "limits": {}, "launchPadExitParameters": []}

    def issue(code, path, message, severity="error", scope="launchpad_route"):
        report["diagnostics"].append({"code": code, "path": path.as_posix(), "message": message,
                                      "severity": severity, "scope": scope})

    try:
        proof, decoded = read_launchpad_save(lpj, readback)
        proof, objects = route_baseline(proof, decoded, geometry is not None,
                                        include_stations=include_stations, include_motion=include_motion)
        route["launchPad"] = proof
    except (OSError, RuntimeError, ValueError) as error:
        issue("launchpad_baseline_invalid", readback, str(error))
        return report
    try:
        renamed, mapping_file = names_for(objects, name_map)
        if mapping_file is not None:
            route["nameMap"] = mapping_file
    except (OSError, RuntimeError, ValueError) as error:
        issue("launchpad_name_map_invalid", name_map, str(error))
        return report

    phase = report if job_dir is None else report["rucReview"]
    for argument, selected in ((configuration, report["selectedConfiguration"]), (cpu, report["selectedCpu"])):
        if job_dir is not None and argument is not None and argument.casefold() != (selected or "").casefold():
            issue("route_selection_mismatch", project, "The requested configuration or CPU differs from the retained build")
            return report
    managers = [row for row in phase["trafficManagers"] if row["manager"] == manager_id]
    if len(managers) != 1:
        issue("route_manager_unavailable", project, f"Could not read exactly one Traffic Manager {manager_id!r}")
        return report
    manager, = managers
    route["assembly"] = manager["assembly"]
    route["objectLists"] = manager["objectLists"]
    route["mappingLists"] = manager["mappingLists"]
    comparison = compare_cycle(objects, renamed, manager)
    route.update(comparison)
    if comparison["missingObjects"]:
        issue("launchpad_object_mismatch", project, "Expected route objects are absent or have different types")
    if comparison["missingEdges"] or comparison["unexpectedEdges"]:
        issue("launchpad_edge_mismatch", project, "Directed edge names, endpoint types or multiplicities differ from the saved LaunchPad cycle")
    if comparison["additionalObjects"]:
        issue("launchpad_additional_objects", project, "Review additional or differently typed objects in the selected manager", "warning")

    severities = {item["severity"] for item in report["diagnostics"]}
    if "error" in severities:
        route["state"] = "issues"
    elif "warning" in severities:
        route["state"] = "review_required"
    else:
        route["state"] = "matched"
        report["evidence"] = "launchpad_route_compiled" if job_dir is not None else "launchpad_route_saved"

    if include_motion and route["state"] == "matched":
        saved_links = phase["savedAssemblyLinks"] if job_dir is not None else None
        limits, issues = review_motion(manager["assembly"], phase["assemblyLinks"], saved_links,
                                       objects, renamed, readback, project)
        report["motionReview"] = limits
        for code, where, message, severity in issues:
            issue(code, where, message, severity, "launchpad_motion")
        if not issues:
            report["evidence"] = "launchpad_motion_compiled" if job_dir is not None else "launchpad_motion_saved"

    if include_recovery and route["state"] == "matched":
        saved_recovery = phase["savedRecoveryEvidence"] if job_dir is not None else None
        recovery, issues = review_recovery(decoded, objects, renamed, manager["recoveryEvidence"],
                                           saved_recovery, readback, project)
        report["recoveryReview"] = recovery
        for code, where, message, severity in issues:
            issue(code, where, message, severity, "launchpad_recovery")
        if not issues:
            report["evidence"] = "launchpad_recovery_compiled" if job_dir is not None else "launchpad_recovery_saved"

    if include_stations and route["state"] == "matched":
        station_review = report["stationReview"]
        legs = station_legs(objects, renamed)
        station_review["legs"] = legs
        unverified = [row["path"][0]["name"] for row in legs if row["modelProcess"]["state"] != "read"]
        if not legs or unverified:
            station_review["state"] = "unverified"
            message = (f"LaunchPad model process is missing or unsupported at {', '.join(unverified)}" if unverified
                       else "The matched cycle has no station to use as a leg boundary")
            issue("launchpad_station_unverified", readback, message, "warning", "launchpad_station")
        else:
            station_review["state"] = "read"
            report["evidence"] = "launchpad_station_compiled" if job_dir is not None else "launchpad_station_saved"

    if geometry is not None:
        try:
            measures, issues = measure_geometry(objects, renamed, manager["positionEvidence"], geometry)
        except (TypeError, ValueError, OverflowError) as error:
            report["geometryReview"]["state"] = "unverified"
            issue("launchpad_geometry_unverified", project, str(error), scope="launchpad_geometry")
        else:
            report["geometryReview"] = measures
            for code, message in issues:
                issue(code, project, message, scope="launchpad_geometry")
            if not issues and route["state"] == "matched":
                report["evidence"] = "launchpad_geometry_compiled" if job_dir is not None else "launchpad_geometry_saved"
    if include_layout:
        layout_path = project
        try:
            layout = linked_layout(phase["assemblyLinks"], manager["assembly"])
            layout_path = Path(layout["path"])
            layout_check, issues = compare_layout(decoded, layout)
        except (ValueError, OverflowError) as error:
            report["layoutReview"]["state"] = "unverified"
            issue("launchpad_layout_unverified", layout_path, str(error), scope="launchpad_layout")
        else:
            report["layoutReview"] = layout_check
            for code, message in issues:
                issue(code, layout_path, message, scope="launchpad_layout")
            if not issues and route["state"] == "matched":
                report["evidence"] = "launchpad_layout_compiled" if job_dir is not None else "launchpad_layout_saved"
    return report
