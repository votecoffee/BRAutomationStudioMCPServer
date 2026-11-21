# Review a two-station inspection cycle trace

Use `cycle_trace_review` for a saved scan capture of one shuttle through an inspection leg, its return and the next held request. The source-station argument selects the inspection leg. The saved LaunchPad cycle and compiled Traffic Manager must define two station legs that return to the starting station. Use the same verified project inputs as the [inspection station trace review](station-trace-review.md).

## Run the review

Use `br-studio operation-help cycle_trace_review` for the request contract. Set the operation and replace the trace path in a copy of the [inspection leg request](station-trace-review.md#bind-the-trace-to-the-route). Run `br-studio run --request <request.json>` or add `--format markdown` for a readable report.

The cycle JSON has `schemaVersion` set to 1 and three objects. To supply the scans and hold from a saved CSV or Automation Studio chart export, use the [scan file workflow](cycle-scan-capture.md).

| Object | Contents |
| --- | --- |
| `inspectionLeg` | A complete [station trace](station-trace-review.md) with the source .lpj and RUC hashes and the captured request and shuttle. |
| `returnLeg` | Return source and destination identifiers, starting monitor sequence and consecutive scan observations. |
| `nextHold` | The first captured hold for a distinct request at the original station after return, with the same shuttle. |

With br-studio 0.59.0 or later, include both `expectedExitVelocityMps` and `expectedExitAccelerationMps2` to compare the outbound Release-edge inputs with the intended command. Use the same LaunchPad inputs as the RUC built with `verifyLaunchPadMotionLimits`. The cycle uses the values from the normalized `inspectionLeg.scans` or the [mapped scan capture](cycle-scan-capture.md#verify-the-motion-inputs-in-the-saved-scan-file). Read `cycleTraceReview.outbound.releaseMotion` for the result. If this check is requested, a supplied HMI pairing is evaluated after the motion check passes.

With br-studio 0.61.0 or later, set `positionToleranceMicrometres` to verify the same shuttle's XY sample on the outbound Release and accepted arrival scans. A requested position check must pass before any HMI pairing. [Sample fields and required RUC evidence](station-trace-review.md#check-position-at-the-two-station-events)

With br-studio 0.63.0 or later, set `waypointToleranceMicrometres` to check for an ordered position sample near each waypoint on both legs. The outbound window lies between Release and arrival. The return window lies between the first occupied source sample and accepted return arrival. Check `cycleTraceReview.outbound.waypointReview` and `cycleTraceReview.returnLeg.waypointReview` for the witnesses. [Waypoint criteria](station-trace-review.md#check-position-near-each-waypoint)

Use `verifySampledSpeed` to compare timed XY displacement rates on both legs with the verified assembly limit. The return interval starts at the first occupied source observation and ends at accepted return arrival. Read `cycleTraceReview.outbound.sampledSpeedReview` and `cycleTraceReview.returnLeg.sampledSpeedReview`. A requested speed check must pass before HMI pairing. [Timestamp and limit requirements](station-trace-review.md#check-sampled-in-plane-speed)

Use `maxSampleIntervalMicroseconds` with the speed check to apply the same maximum acquisition gap to both legs. The phase identifies the leg where a gap left the review incomplete. Capture the position stream again at the required cadence before attempting the HMI comparison. [Interval diagnostics](station-trace-review.md#limit-the-interval-between-position-samples)

## Compare timestamps at the handoff between legs

With br-studio 0.66.0 or later, set `verifyPositionTimeHandoff: true` with `verifySampledSpeed: true` when the capture uses one monotonic acquisition clock across both legs. If outbound arrival and the initial return-source occupancy use the same PLC scan, they must have the same position acquisition time. If occupancy is recorded on a later scan, its time must increase. Preserve the recorded clock and timestamps in both windows. [Capture column mapping](cycle-scan-capture.md#check-station-positions-in-the-scan-file)

Read `cycleTraceReview.positionTimeHandoff` for both scan and timestamp pairs and their signed difference in microseconds. A conflict fails the cycle in phase `position_time_handoff` before any supplied HMI comparison. The per-leg maximum sample interval applies inside each travel window. The handoff difference may include time held at the station.

## Record the return leg

Use the same PLC scan index as the outbound trace. Start while the inspected shuttle occupies the return source station. This may overlap the outbound capture from its accepted arrival onward. A return starting after the outbound interval must begin at the next scan.

In `returnLeg`, set `sourceStation` and `destinationStation` to their imported identifiers. Set `monitorSequenceAtStart` to the last consumed destination event sequence before watching the return. Include each subsequent scan in `scans`.

- Record `scan`, `sourceHealthy` and `monitorHealthy`. Base validity on the active source observation and destination monitor, including queue continuity. Set `occupyingShuttleId` to the positive ID of the single shuttle at the return source. Use `null` after observing valid empty occupancy.
- Use the same `detections` list as the station trace. Preserve the station, shuttle, sequence and event enum. The cycle check requires a fresh `mpTRAFFIC_SH_ARRIVAL` at the original inspection station. It accepts either order of observing the return source clear and the destination arrival.
- Include Boolean `error` and integer `statusId` on a return error. The reviewer preserves that status and stops the return check.

Source occupancy and destination arrival support this check whether the recipe drives the return or station control requests it. Use the normalized source-clear observation for the return. The reported waypoint names come from the compiled route. Verify actual waypoint traversal separately when needed.

## Record the next request

Capture `nextHold` after the outbound interval and both return observations. Use a scan covered by the return capture or its immediately following scan. Record `scan`, `station`, `shuttleId`, `requestId`, `held`, `release` and `resultAccepted`. The new request needs a different ID, the same returned shuttle and `held: true` with `release` and `resultAccepted` clear.

Keep the raw PLC export, normalized trace, review output and RUC job together. The review records the trace SHA-256, compiled paths and decisive scan indexes. A gap or missing witness yields `incomplete`. An identity conflict, premature hold or recorded adapter error yields `issues`.

## Verify positions on return and at the next hold

With br-studio 0.62.0 or later, set `returnPositionToleranceMicrometres` to the maximum difference on each axis. Use the same LaunchPad inputs as the RUC built with `verifyLaunchPadPositions` and explicit X and Y in `launchPadReadAxes`. Capture the same shuttle's valid XY sample on the first occupied return-source scan, the accepted return-arrival scan and the next held request scan. Use the [station sample fields](station-trace-review.md#check-position-at-the-two-station-events) in the selected `returnLeg.scans` and `nextHold` rows. Supply the outbound position tolerance as well when the capture includes both outbound witnesses.

Read `cycleTraceReview.returnPositionReview.samples` for the three witnesses. A stale sample or unavailable position leaves the check incomplete. A different shuttle or a position outside the tolerance fails it. A requested position check must pass before any supplied HMI pairing. [Map the capture columns](cycle-scan-capture.md#check-station-positions-in-the-scan-file)

## Match the HMI reopen to that hold

Build one PC simulation RUC with the selected Traffic Manager route and read-only HMI status bindings. Configure the [status smoke](arsim-status-smoke.md) to wait for the next hold, using the request ID, shuttle ID, held phase and release count expected in that run. Size its OPC UA timeout for the inspection cycle. Hold the new request across both browser documents and direct read windows.

Collect the scan trace during that same disposable runtime with your project's trace instrumentation. Record the observed heartbeat at the next hold in `nextHold.heartbeat` and the actual status integers in `nextHold.phase` and `nextHold.releaseTotal`. Use a nonnegative heartbeat and release count. Save the ARsim `sessionId` from that run's `session.json` with the raw trace. The normalized cycle trace adds this top-level object.

```json
{
  "hmi": {
    "sessionId": "<recorded from session.json>",
    "heartbeatIdentifier": "::Status:Heartbeat",
    "bindings": {
      "requestId": "::Status:RequestId",
      "shuttleId": "::Status:ShuttleId",
      "phase": "::Status:Phase",
      "releaseTotal": "::Status:ReleaseTotal"
    }
  }
}
```

Set the optional `arguments.hmiStatusReport` to the completed `arsim_status_smoke` JSON response from that runtime. The path resolves from the cycle request directory. Use br-studio 0.29.0 or later to capture and pair the reports.

- The cycle and HMI results must identify the same build, RUC and runtime session. Keep the trace's original `captureKind` of `arsim` or `synthetic`.
- The heartbeat at the hold must be at or before the last direct pre-browser sample. The direct samples bracket increasing browser samples across the initial, blank and reopened pages.
- The four status values must agree with the hold in both OPC UA read windows and both rendered documents. The reviewer checks the matched compiled bindings, node types and read-only access and quality of the OPC UA samples.

If the status smoke retained a scan file, point `arguments.scanCsv` to that file and use its SHA-256 in the scan descriptor. The paired review checks its bytes against the same session's receipt. Set `requireSessionScanCapture` to true when this acquisition is part of acceptance. [Retain a scan file during the HMI status smoke](arsim-status-smoke.md#retain-a-scan-file-from-the-same-runtime)

Use `cycleHmiReview.state` for the pairing outcome. A failed pairing yields `issues` while preserving an independently matched cycle. Retain both input reports and the raw capture provenance. The joined check establishes consistency across supplied observations. A synthetic trace retains its synthetic capture classification.

## Verify the held request through the reopen

With br-studio 0.67.0 or later, record each consecutive PLC scan from the first new hold through the final direct heartbeat after the browser reopen. Use [`scanCapture.holdThroughScan`](cycle-scan-capture.md#keep-recording-the-next-held-request) with a scan file. In a normalized JSON trace, set a top-level `holdContinuation` with `throughScan` and `scans`. Put each subsequent scan, starting at `nextHold.scan + 1`, in that array. Use the same fields as `nextHold`, including `phase`, `releaseTotal` and `heartbeat`.

The held request, station, shuttle, phase and release total must agree on all scans. Hold stays active and release and result acceptance stay clear. The heartbeat is nondecreasing. Read `cycleTraceReview.holdContinuation` for the checked range. Missing scans leave the review incomplete. A recorded state change fails it before HMI pairing.

With a supplied HMI report, the last held heartbeat must reach the final direct OPC UA heartbeat after the reopen. Read `cycleHmiReview.holdCoverage` for both values. Use `requireSessionScanCapture` with the raw scan file retained by the HMI session when the original file is part of acceptance.

## Verify the cycle's Assembly6D widget

With br-studio 0.68.0 or later, set `requireHmiAssemblyWidget: true` with `hmiStatusReport`. In the [status smoke request](arsim-status-smoke.md#select-the-assembly6d-widget-and-layout), select the Assembly6D DOM ID and enable the start-page and layout checks. Use the RUC from the cycle review. The startup widget must link to the compiled assembly and Traffic Manager used for the observed cycle.

Read `cycleHmiReview.assemblyWidget` for the selected widget, compiled content, startup map and both layout paths. The initial and reopened documents each need the widget, a successful startup-map request, both expected layout SVGs and a rendered SVG with bounds. A mismatch fails the HMI pairing while preserving the independent cycle result.

## Match the captured layout geometry to the cycle

With br-studio 0.69.0 or later, enable `captureLayoutAssets` and `requireStartPage` in the status smoke request. Review the two SVGs saved by that run with [`layout_svg_review`](layout-svg-review.md#run-the-comparison) and save the complete JSON response. Pass that response as `hmiLayoutSvgReport` alongside `hmiStatusReport` in the cycle review. This also runs the Assembly6D widget check.

The cycle, status and geometry reports must identify the same build and RUC. The geometry report must match the widget's assembly and manager, and both SVG hashes must match the capture from the status report's owned HTTP listener. Read `cycleHmiReview.layoutGeometry` for the layout ID and the two hashes. Browser observations cover both documents. The geometry review covers the SVG bodies saved by the status probe's HTTP capture.

If the geometry review includes `verifyMappingLineEndpoints`, the paired layout receipt also carries that result. [Check the drawn mapping endpoints](layout-svg-review.md#check-the-drawn-mapping-endpoints)
