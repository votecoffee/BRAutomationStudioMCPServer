# Review an inspection station trace

Use `station_trace_review` to check one recorded inspection leg against a saved LaunchPad route and its compiled Traffic Manager. The operation reads the supplied JSON and build evidence offline. It checks the station path and the release and arrival observations for one request.

## Bind the trace to the route

Start with a completed AS4 RUC build and a [compiled station path review](launchpad-station-review.md). Use the imported source station name in the request. The review derives its next station from the matched LaunchPad cycle and compiled mappings.

```json
{
  "schemaVersion": 1,
  "workspace": "inspection-leg",
  "operation": "station_trace_review",
  "arguments": {
    "project": "project/Example.apj",
    "jobDirectory": "retained-build",
    "managerId": "DemoTraffic",
    "sourceStation": "Load",
    "launchPadProject": "Route.lpj",
    "launchPadReadback": "Route-readback.json",
    "trace": "first-leg.json"
  }
}
```

Save the request and trace together, outside the project tree. Paths resolve from the request directory. Run `br-studio run --request <request.json>` and use `--format markdown` for a readable report. If the source was renamed during import, supply the same `nameMap` used for the matched station review.

The trace is a UTF-8 JSON object with `schemaVersion` set to 1. Populate these fields from the raw capture and build receipts.

| Field | Content |
| --- | --- |
| `captureKind` | `synthetic`, `arsim` or `controller`. Keep the original acquisition receipt with its provenance. |
| `managerId`, `sourceStation`, `destinationStation` | Imported identifiers for the exact leg. |
| `launchPadSha256`, `rucSha256` | Source .lpj hash from the decoder receipt and verified archive hash from the build. |
| `requestId`, `shuttleId` | Positive integers captured for the held transaction. |
| `monitorSequenceBeforeRelease` | Nonnegative last consumed destination-monitor sequence immediately before raising Release. Start after the queue has been drained. |
| `scans` | Consecutive PLC scan samples from the held request and low release through departure and arrival. |

## Normalize the scan samples

Keep the raw capture alongside the JSON. Include every scan in the interval, starting with the selected shuttle and clear release outputs. Use PLC scan indexes to order the interval. Browser polling supplies separate HMI evidence.

- Set `scan` to the nonnegative scan index and `requestId` to the active latched request. Use `selectedShuttleId` for the source station's selected shuttle and `null` when the source selection is empty.
- Record the Boolean fields `inspectionBusy`, `routePermitted`, `stationClear`, `release`, `releaseBuffered`, `releaseDone`, `releaseAborted`, `error` and `monitorHealthy`. Capture station control inputs and outputs for the same function block call. Set `monitorHealthy` according to monitor validity and queue continuity. Include the integer `statusId` when Abort or Error is present.
- Include `inspectionResult` when a terminal result is observed. Keep the reported `requestId` and `shuttleId`, the matching acknowledgment state as `acknowledged`, and the terminal disposition as `approved`. Preserve stale results with their original IDs.
- Include `detections` as a list of destination-monitor observations for that scan. Each item needs the application-issued positive `sequence`, imported `station`, `shuttleId` and the raw enum name in `event`. Increment the sequence once for each consumed monitor event and retain that sequence when a captured observation repeats. Read the event after consuming it from the monitor. [Station monitor behavior](https://help.br-automation.com/#/en/4/motion/mapp_6d/bibliotheken/mptraffic/function_block/mptrafficstationmonitor/fb_mptrafficstationmonitor_functiondescription.html)

For a trace with sequence snapshots, include `monitorSequenceBeforeScan` and `monitorSequenceAfterScan` on each row. Use the destination monitor's application sequence before and after consuming the recorded detections. The review reports inconsistent sequence evidence as incomplete. [Scan capture checkpoints](cycle-scan-capture.md#check-the-consumed-event-sequence-in-every-scan)

## Check the motion inputs on the Release edge

With br-studio 0.58.0 or later, supply both `expectedExitVelocityMps` and `expectedExitAccelerationMps2` in the request as finite decimal strings. Use the intended station command values in m/s and m/s² from the reviewed test. Build with `verifyLaunchPadMotionLimits` and reuse the same LaunchPad save, decoded readback and name map. [Pin the assembly limits](native-build.md#pin-the-explicit-in-plane-motion-limits)

Capture the actual function block inputs as `exitVelocityMps` and `exitAccelerationMps2` on the scan that raises Release. Store each as a decimal string in the same units. For example, a trace row can include `"exitVelocityMps": "0.6000"` and `"exitAccelerationMps2": "6.0"`. Equivalent decimal spellings compare equal. Values from a later scan do not fill a gap on the Release edge.

Read `stationTraceReview.releaseMotion` for the Release scan, expected and recorded inputs, and the compiled limits. A missing witness leaves the motion check incomplete and a different value fails it. The expected inputs verify the recorded command. Use separate measured position or velocity evidence to assess the actual movement.

## Check position at the two station events

With br-studio 0.61.0 or later, set `positionToleranceMicrometres` in the request to the allowed difference on each axis. Use the same LaunchPad save, readback and name map as the RUC built with `verifyLaunchPadPositions` and `"launchPadReadAxes": ["X", "Y"]`. Record the actual position in the assembly's Automation Studio coordinate frame. [Verify coordinates](native-build.md#pin-the-reviewed-traffic-positions) · [Verify read axes](native-build.md#pin-the-assembly-io-and-read-axes)

On the Release edge and accepted destination-arrival scan, record `positionSampleScan` and `positionShuttleId` from the actual position sample, `positionValid` as a Boolean, and `positionXM` and `positionYM` as decimal strings in metres. Keep the sample's actual scan and identity when carrying values between tasks. A stale or unavailable observation leaves the check incomplete. A wrong shuttle or a position outside the tolerance fails it.

Read `stationTraceReview.positionReview.samples` for the two station positions from the compiled RUC, the recorded samples and signed per-axis differences in micrometres. Capture intermediate samples separately to establish trajectory or waypoint traversal.

## Check position near each waypoint

With br-studio 0.63.0 or later, add `waypointToleranceMicrometres` to the request. Set it to the permitted per-axis difference for the reviewed path and sampling interval. Use the same RUC evidence and position fields as the station position check. Keep the synchronized samples strictly between the Release and arrival scans.

The reviewer looks for a distinct same-shuttle sample near each waypoint in the compiled order. A witness must be within tolerance on both axes and outside the tolerance of every other compiled object on the route. Read `stationTraceReview.waypointReview` for the ordered witnesses, pending waypoints and counts of ambiguous or unusable samples. Missing ordered evidence leaves the check incomplete. Use timed position samples for velocity and clearance evaluation.

## Check sampled in-plane speed

With br-studio 0.64.0 or later, set `verifySampledSpeed` to true. Use the same LaunchPad inputs and the RUC built with `verifyLaunchPadPositions`, explicit X and Y read axes, and `verifyLaunchPadMotionLimits`. Keep valid same-shuttle XY positions from Release through accepted arrival. Add `positionTimeMicroseconds` as the nonnegative integer acquisition time on a common monotonic clock. Retain the captured sample times from the PLC.

The reviewer needs a usable position and time at each end of the window. It divides straight-line XY displacement by elapsed time between consecutive usable observations and compares each mean with the compiled `InPlaneVelLim`. Read `sampledSpeedReview` for the largest sampled mean, its scan interval, the longest time gap and any exceedance. A decreasing or repeated sample time fails the check. Use the capture's time resolution when assessing brief velocity peaks.

## Limit the interval between position samples

With br-studio 0.65.0 or later, supply `maxSampleIntervalMicroseconds` with `verifySampledSpeed: true` to enforce a maximum elapsed time between consecutive usable X/Y samples. Use a positive integer from the test's timing requirements. An interval equal to the maximum passes the timing check. Missing or unusable intermediate samples can widen the interval.

Read `maxIntervalMicroseconds` for the longest observed gap, `overlongIntervals` for the count above the maximum and `firstOverlongInterval` for the first pair of scans and elapsed time. An overlong interval makes the check incomplete. An interval whose mean exceeds the compiled velocity limit still fails the speed check.

## Interpret the result

- `matched` requires one Release edge with the approved request and shuttle, route permission, station clear and inspection busy clear. The same leg needs ReleaseDone and a fresh `mpTRAFFIC_SH_ARRIVAL` at the expected station for that shuttle. Either observation may appear first in the PLC trace after release.
- Read `bufferedScan` separately. A missing Buffered observation is recorded as null. An Abort or Error takes priority over Done or arrival on the same scan. The report retains the first raw adapter status.
- `incomplete` means a required observation or continuity is missing. A stale sequence, wrong shuttle, pass-through or manual assignment cannot satisfy arrival. `issues` identifies conflicts such as a second Release edge, a changed request, invalid trace identity or adapter fault.
- A matched report establishes consistency within the captured leg. Use the [inspection cycle review](cycle-trace-review.md) for the return and next held request, and collect HMI reconnect evidence separately.
