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
