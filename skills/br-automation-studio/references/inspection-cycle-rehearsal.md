# Rehearse one ACOPOS 6D inspection cycle with its HMI

Use this workflow with br-studio 0.77.0 or later for a saved AS4 PC simulation project, a two-station LaunchPad route and a PLC-hosted mapp View page. Work in one run directory with a reviewed project and build as inputs. Keep installation paths, project identifiers and captures in the local run directory.

## Qualify the project and build

1. Check the installed packages and simulator with `simulation_environment`. Run `simulation_preflight` with the exact configuration, CPU, target mapp 6D family and native LaunchPad export. Record the saved project and export identity. Review the selected `McAcp6D` and `MpTraffic` declarations for the adapter signals being captured. [Simulation preflight](simulation-preflight.md)
2. Compare the LaunchPad save and decoded readback with the native export. After import, verify the saved Traffic Manager, linked assembly and the two station paths. Record the imported station names and retain the name map if the import changed any identifiers. [Export and route review](launchpad-route-review.md)
3. Use the same project and route inputs for `build_plan` and `build_execute`. Pin `launchPadWidgetId` if the default page should show the reviewed route. Enable the build checks needed for the evidence selected below. Retain `plan.json`, `job.json`, `builder.log`, the RUC and `launchpad/` inputs in the build directory. Use the native job status and RUC readback to assess a warnings-only build. [Native build](native-build.md) · [RUC review](ruc-review.md)
4. Confirm the compiled default page, widget links, read-only status bindings and `6DData` file device. For the scan producer, record the PLC task scan, inspection result identity and Release, the consumed outbound and return monitor events, and the next held request through the post-reopen window. [Bindings](ruc-binding-review.md) · [File device](layout-device-review.md) · [Required scan columns](cycle-scan-capture.md#bind-the-scan-file)

## Keep one directory for the run

Copy the JSON examples into the names shown. Replace the example paths, IDs, status integers and scan numbers with values from the selected project and run. Relative paths resolve from each request file. Keep `session/` outside `project/` and `build/`. That session directory must be new when the status command starts.

```text
run/
  project/Example.apj
  build/                           Retained native job, RUC and launchpad/ inputs
  status-bindings.json
  status-request.json
  session/
    status-response.json
    session.json
    scan-capture.csv
    layout-assets/assembly.svg
    layout-assets/trafficManager.svg
  cycle-trace.json
  cycle-request.json
  cycle-response.json
```

## Capture the HMI and cycle together

Select the four read-only status bindings for the **next** held request at the starting station after return. Set their expected integers for a deterministic rehearsal and select a separate read-only heartbeat that advances through the capture. Use the compiled identifiers. In these requests, `widgetId` means the widget ID within its content, whereas `assemblyWidgetId` is the full `ContentId_WidgetId` DOM ID. [Status smoke setup](arsim-status-smoke.md#select-the-expected-status)

Save as `status-bindings.json`.

```json
{
  "schemaVersion": 1,
  "bindings": [
    {"widgetId": "RequestValue", "stringIdentifier": "::Status:RequestId", "expectedValue": 18, "role": "requestId"},
    {"widgetId": "ShuttleValue", "stringIdentifier": "::Status:ShuttleId", "expectedValue": 7, "role": "shuttleId"},
    {"widgetId": "PhaseValue", "stringIdentifier": "::Status:Phase", "expectedValue": 10, "role": "phase"},
    {"widgetId": "ReleaseValue", "stringIdentifier": "::Status:ReleaseTotal", "expectedValue": 4, "role": "releaseTotal"}
  ]
}
```

Save as `status-request.json`. The PVI and Chrome paths are examples. Use actual installed executables and timing budgets that cover your test.

```json
{
  "schemaVersion": 1,
  "workspace": "inspection-rehearsal",
  "operation": "arsim_status_smoke",
  "arguments": {
    "project": "project/Example.apj",
    "jobDirectory": "build",
    "pviTransfer": "C:/Tools/PVI/PVITransfer.exe",
    "sessionDirectory": "session",
    "browserExecutable": "C:/Tools/Chrome/chrome.exe",
    "visualizationId": "Overview",
    "contentId": "Main",
    "widgetId": "HeartbeatValue",
    "stringIdentifier": "::Status:Heartbeat",
    "statusBindingFile": "status-bindings.json",
    "assemblyWidgetId": "Main_AssemblyOverview",
    "requireStartPage": true,
    "captureLayoutAssets": true,
    "scanCaptureFile": "cycle.csv",
    "timeoutSeconds": 90,
    "opcUaTimeoutSeconds": 120,
    "browserTimeoutSeconds": 60,
    "scanCaptureTimeoutSeconds": 75
  }
}
```

From the run directory in PowerShell 7, save the JSON result at the run root. Move the completed response next to the retained scan once the command succeeds. Use the installed executable's full path in place of `br-studio` if it is outside PATH.

```powershell
br-studio run --request ./status-request.json | Set-Content -LiteralPath ./status-response.json -Encoding utf8NoBOM
if ($LASTEXITCODE -eq 0) {
    Move-Item -LiteralPath ./status-response.json -Destination ./session/status-response.json
}
```

Coordinate the matched Planar Motor Simulator and inspection stimuli with the owner of the ARsim session that the status probe opens. Keep the Logger connection evidence with that session. The probe waits for the expected held status, reads the initial and reopened browser documents, performs the direct reads and retains the finalized scan CSV and SVGs before cleanup. Have the producer record through the last post-reopen read, then publish `cycle.csv` in the owned ARsim USER device. Review the completed JSON for `result.arsimSmoke.sessionId`, cleanup, the browser layout capture and the retained scan receipt. [Same-runtime scan capture](arsim-status-smoke.md#retain-a-scan-file-from-the-same-runtime)

## Review the saved SVGs and PLC scans

Save as `cycle-trace.json`. Use the recorded monitor baselines, captured request and shuttle IDs, imported names and actual scan windows. The `captureKind` example applies to a real ARsim acquisition. Keep the original classification when replaying evidence. The strict review takes the LaunchPad and RUC hashes from the matched pinned build. It obtains the scan hash, session ID, heartbeat and status bindings from the role-labeled status receipt and verifies the CSV bytes. [Pinned input checks](cycle-trace-review.md#use-the-pinned-build-for-inspection-hashes) · [Supply a binding map for an older receipt](cycle-trace-review.md#match-the-hmi-reopen-to-that-hold)

```json
{
  "schemaVersion": 1,
  "inspectionLeg": {
    "schemaVersion": 1,
    "captureKind": "arsim",
    "managerId": "ExampleTraffic",
    "sourceStation": "Inspect",
    "destinationStation": "Outfeed",
    "requestId": 17,
    "shuttleId": 7,
    "monitorSequenceBeforeRelease": 51
  },
  "returnLeg": {
    "sourceStation": "Outfeed",
    "destinationStation": "Inspect",
    "monitorSequenceAtStart": 80
  },
  "scanCapture": {
    "format": "csv",
    "inspectionScans": [100, 107],
    "returnScans": [107, 110],
    "nextHoldScan": 111,
    "holdThroughScan": 115
  }
}
```

The example uses canonical CSV column names and named detection events. Set `scanCapture.columns` for differing headers, `detectionSlots` for multiple consumed events on a scan, and `monitorCheckpoints` if the capture includes the before-and-after counters. Use `eventEncoding: "project-mptraffic"` for the selected verified library's numeric event values, or preserve an explicit versioned event map. Choose the first new hold and every following sample through at least the final direct heartbeat after reopening. [Scan windows and mapping](cycle-scan-capture.md) · [Hold window](cycle-trace-review.md#verify-the-held-request-through-the-reopen)

Save as `cycle-request.json`. If the build retained a name map, supply the same file as `nameMap`.

```json
{
  "schemaVersion": 1,
  "workspace": "inspection-rehearsal",
  "operation": "cycle_trace_review",
  "arguments": {
    "project": "project/Example.apj",
    "jobDirectory": "build",
    "managerId": "ExampleTraffic",
    "sourceStation": "Inspect",
    "launchPadProject": "build/launchpad/source.lpj",
    "launchPadReadback": "build/launchpad/readback.json",
    "trace": "cycle-trace.json",
    "hmiStatusReport": "session/status-response.json",
    "verifyCapturedLayout": true,
    "verifyMappingLineEndpoints": true,
    "verifyTrafficLabels": true,
    "requireSessionScanCapture": true
  }
}
```

The review uses `scan-capture.csv` and `layout-assets/` beside the selected status response. Supply `scanCsv` explicitly when the retained scan copy is stored elsewhere. `verifyCapturedLayout` enables the Assembly6D widget and SVG checks. Set the mapping endpoint and traffic label flags to match the test and captured format. Add motion checks when their source evidence is present and the test defines the expected values.

| Check | Request inputs | Evidence needed |
| --- | --- | --- |
| Release setpoints | `expectedExitVelocityMps` and `expectedExitAccelerationMps2` as decimal strings | Both inputs sampled on the Release edge and a RUC with pinned motion limits |
| Station and return positions | `positionToleranceMicrometres` and `returnPositionToleranceMicrometres` | Approved tolerances, pinned positions, explicit X/Y read axes, valid same-shuttle XY at each boundary |
| Ordered waypoints | `waypointToleranceMicrometres` | Pinned waypoint positions and synchronized intermediate samples |
| Timed sampled speed | `verifySampledSpeed` and optional `maxSampleIntervalMicroseconds` | Pinned positions and motion limits, explicit X/Y read axes, timed positions in both legs |
| Shared acquisition clock | `verifyPositionTimeHandoff` with `verifySampledSpeed` | One monotonic clock across outbound arrival and return occupancy |

Run the combined cycle review and save its response.

```powershell
br-studio run --request ./cycle-request.json | Set-Content -LiteralPath ./cycle-response.json -Encoding utf8NoBOM
$LASTEXITCODE
```

## Read the handoff

| Receipt | Fields to record |
| --- | --- |
| Build and RUC | Native job status and warnings, package SHA-256, matched route and pinned inputs |
| Status | `result.arsimSmoke.sessionId`, retained scan SHA-256, both browser documents and cleanup |
| Geometry | `result.layoutSvgReview.state`, assembly and manager, `statusCapture`, both SVG hashes, requested line and label results |
| Cycle | `result.cycleTraceReview.state` and phase, derived hashes in `buildBinding`, accepted arrival and next hold scans, requested motion checks |
| Same-run HMI | `result.cycleHmiReview.state`, `scanCapture`, `holdCoverage`, `assemblyWidget` and `layoutGeometry` |

Record an incomplete scan window or unsupported SVG format with its diagnostic and the last accepted evidence. Keep the JSON receipts alongside the exact files they reference. The first rehearsal handoff covers the saved project, compiled build and observed simulation. Record hardware observations separately if they are later collected.
