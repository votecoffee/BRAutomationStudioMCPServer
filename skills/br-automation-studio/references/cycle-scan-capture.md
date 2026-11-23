# Review saved PLC scan samples for a two-station cycle

Use br-studio 0.30.0 or later to pass a saved scan file directly to [cycle_trace_review](cycle-trace-review.md). The reviewer reads the original file and projects explicitly selected PLC scan indexes into the existing cycle checks. It records the raw file's SHA-256, format, record count and scan windows.

## Capture one sample per PLC task cycle

Keep an increasing PLC scan counter from the task that owns the station observations. Sample all selected signals in a common capture, once per task cycle. Include the held inspection request and approved result, Release through Done and arrival, occupancy at the return station through clearance and arrival, and the next valid held request. Keep the applied monitor sequence baselines with the raw capture. Use an acquisition that preserves every event consumed by each monitor during the scan, in queue order.

The operation accepts either format. Preserve the raw file bytes after capture.

| `scanCapture.format` | Shape |
| --- | --- |
| `csv` or omitted | A single header row and one record per PLC scan. Use UTF-8 with optional BOM. Select comma, semicolon or tab with `delimiter`, which defaults to comma. |
| `as-chart` | Automation Studio `CHART-DATA-ASCII` V2.0 comma-separated data, with one metadata line and one timestamp/value pair per signal. Map columns using their exact `TITLE` strings. The reviewer checks the declared sample count, increasing timestamps and exact timestamp agreement across all signals in each sample. Values come from the saved data columns. |

Signal timestamps establish alignment. The PLC scan counter establishes continuity and the selected window boundaries. Use exact integer signals for IDs, sequence numbers and status. Whole-valued decimal text such as `17.0000` is accepted for numeric fields.

## Bind the scan file

1. Add `arguments.scanCsv` to the `cycle_trace_review` request and point it to the capture. Paths resolve from the request directory. Keep `arguments.trace` pointing to the cycle JSON.
2. Keep the existing `inspectionLeg` and `returnLeg` headers with their captured baseline sequences. Use the original `captureKind`, source .lpj hash and compiled RUC hash from those receipts. Leave out `inspectionLeg.scans`, `returnLeg.scans`, `nextHold` and `holdContinuation` when using a scan file.
3. Add the `scanCapture` object below to the cycle JSON. Hash the exact file with `(Get-FileHash -LiteralPath <capture.csv> -Algorithm SHA256).Hash.ToLowerInvariant()`. Choose the inclusive inspection and return windows and the hold scan from the original capture.

With br-studio 0.72.0 or later, a [strict same-session review](cycle-trace-review.md#use-the-status-receipt-for-scan-identity) can read the scan hash and HMI session ID from the status receipt when those fields are omitted.

```json
{
  "scanCapture": {
    "format": "as-chart",
    "sha256": "<lowercase SHA-256>",
    "inspectionScans": [100, 106],
    "returnScans": [106, 109],
    "nextHoldScan": 110,
    "columns": {
      "scan": "TARGET_DATA   Capture.Scan",
      "inspection.release": "TARGET_DATA   Capture.OutboundRelease"
    }
  }
}
```

The field names below are the default column names. In `columns`, map every field whose captured header or AS chart title differs. Additional recorded columns may be present. Each selected field needs a distinct column.

| Witness | Fields |
| --- | --- |
| PLC scan | `scan` |
| Outbound station | `inspection.requestId`, `inspection.selectedShuttleId`, `inspection.inspectionBusy`, `inspection.routePermitted`, `inspection.stationClear`, `inspection.release`, `inspection.releaseBuffered`, `inspection.releaseDone`, `inspection.releaseAborted`, `inspection.error`, `inspection.monitorHealthy`, `inspection.statusId` |
| Inspection result | `inspection.resultPresent`, `inspection.result.requestId`, `inspection.result.shuttleId`, `inspection.result.acknowledged`, `inspection.result.approved` |
| Optional release motion | `inspection.exitVelocityMps` and `inspection.exitAccelerationMps2` |
| Optional station positions | `inspection.positionSampleScan`, `inspection.positionShuttleId`, `inspection.positionValid`, `inspection.positionXM` and `inspection.positionYM` |
| Optional position acquisition time | `inspection.positionTimeMicroseconds` and `return.positionTimeMicroseconds` |
| Return source | `return.sourceHealthy`, `return.occupyingShuttleId`, `return.monitorHealthy`, `return.error`, `return.statusId` |
| Destination monitors | Each of `inspection.detection` and `return.detection` needs `.sequence`, `.station`, `.shuttleId`, `.event`. |
| Next held request | `hold.station`, `hold.requestId`, `hold.shuttleId`, `hold.held`, `hold.release`, `hold.resultAccepted` |
| HMI pairing | Include all of `hold.phase`, `hold.releaseTotal` and `hold.heartbeat` when supplying `hmiStatusReport`. Keep the cycle JSON's [`hmi` object](cycle-trace-review.md#match-the-hmi-reopen-to-that-hold). |

The first monitor slot uses `.detection`. For multiple consumed events in one scan, declare the recorded slot counts in `detectionSlots`, for example `{"inspection": 2, "return": 1}`. Add the same four fields for each additional slot using `.detection2`, `.detection3` and subsequent suffixes. Keep the event order from the capture and set unused slot sequences to `0`.

Use `0` for the absent selected or occupying shuttle and for a slot with no observed monitor event. Boolean cells accept `TRUE`, `FALSE` or numeric 0/1. On an observed event, use the sequence, station, shuttle and enum preserved by the capture. For numeric station codes, add `stationNames` to `scanCapture` with exact raw text keys and the imported station names as values. For numeric detection codes, use the project declaration below or supply a reviewed `eventNames` map with the exact capture cell text as keys. For example, `{"1.0000": "Inspect"}` maps a recorded station code once the capture defines that correspondence.

Read only the window-specific columns for each selected phase. A fault needs its raw `statusId`. The result details are read when `resultPresent` is true. The detection details are read when the event sequence is positive. All columns still need headers so missing signal definitions surface before review.

## Keep recording the next held request

With br-studio 0.67.0 or later, set `scanCapture.holdThroughScan` to the inclusive end of the hold window, later than `nextHoldScan`. Record every PLC scan from the first hold through that end, with all six next-hold fields and the three HMI fields in the table. Use the same heartbeat exposed by the HMI and capture a window within one nondecreasing span of that counter. [Hold and HMI review criteria](cycle-trace-review.md#verify-the-held-request-through-the-reopen)

With br-studio 0.78.0 or later, use `holdThroughScan: "last"` when the capture ends while the next request is still held. The review uses every recorded scan after `nextHoldScan` through the end of the file and reports the resolved index in `cycleTraceReview.scanCapture.holdThroughScan`. It requires at least one subsequent scan. Use an explicit scan index when the file continues past the held interval.

## Verify the motion inputs in the saved scan file

With br-studio 0.59.0 or later, record the outbound station's `ExitVelocity` and `ExitAcceleration` in the same PLC task sample as Release. The optional default column names are `inspection.exitVelocityMps` and `inspection.exitAccelerationMps2`. Map other CSV headers or AS chart titles under `scanCapture.columns`. Preserve the original decimal strings including exponent notation and trailing zeros. Use m/s and m/s².

Pass both expected inputs as decimal strings to `cycle_trace_review` and use the RUC and LaunchPad files from a build with `verifyLaunchPadMotionLimits`. The check uses the exact Release-edge row. A blank cell or an absent observation on that scan leaves the motion check incomplete. Read `cycleTraceReview.scanCapture.releaseMotionColumns` for the source columns and `cycleTraceReview.outbound.releaseMotion` for the comparison. [Expected inputs](station-trace-review.md#check-the-motion-inputs-on-the-release-edge)

## Check station positions in the scan file

Map the five optional position columns to the recorded PLC sample fields and set `positionToleranceMicrometres` in the cycle review request. Check the source position on the Release edge and the destination position on the accepted arrival scan. Use the position sample's own scan index and shuttle ID. Preserve coordinate text in metres and the sample validity flag. Blank values are permitted where the capture provides no observation. Read `cycleTraceReview.scanCapture.positionColumns` for the selected headers and `cycleTraceReview.outbound.positionReview` for the comparison. [Position checks and required RUC evidence](station-trace-review.md#check-position-at-the-two-station-events)

For the return and hold check, map the same five field names under the `return.` and `hold.` prefixes and set `returnPositionToleranceMicrometres`. You can map the same physical column to the same position field across phases. For example, use one captured X column for `inspection.positionXM`, `return.positionXM` and `hold.positionXM`. Keep each different field in its own column. [Return and hold witnesses](cycle-trace-review.md#verify-positions-on-return-and-at-the-next-hold)

For waypoint evidence, keep intermediate position rows in both legs and pass `waypointToleranceMicrometres` in the request. Use the same position columns as for station witnesses. [Check both waypoint sequences](cycle-trace-review.md#run-the-review)

For sampled speed, add the recorded monotonic position acquisition time under the `inspection.` and `return.` prefixes. Map the time signal explicitly when the capture header differs and set `verifySampledSpeed` in the request. Use a common clock within each leg and retain the original integer microsecond values. [Sampled speed criteria](station-trace-review.md#check-sampled-in-plane-speed)

Set `maxSampleIntervalMicroseconds` in the review request to enforce the permitted time gap between usable position samples. Keep the original scan rows when a position sample is unavailable so the report can distinguish the missing observation from a missing PLC scan. [Sampling interval check](station-trace-review.md#limit-the-interval-between-position-samples)

For a shared clock, add `verifyPositionTimeHandoff` to compare the last outbound and first return timestamps. When both windows use the same recorded acquisition signal, map both time fields to that one column. [Handoff rules](cycle-trace-review.md#compare-timestamps-at-the-handoff-between-legs)

## Decode events from the saved project and RUC

Use the [enum declaration lookup](build-preflight.md#read-explicit-enumeration-values) to inspect the selected library's event map during preflight. The scan review below verifies the definition against the retained build and RUC.

With br-studio 0.33.0 or later, set `scanCapture.eventEncoding` to `"project-mptraffic"` to verify event codes against the selected build. Leave out `eventNames` in this mode. The decoder reads the explicit `MpTrafficShDetectionEnum` values from the project's registered `Logical/Libraries/MpTraffic/MpTraffic.typ`. It checks that the library descriptor's version agrees with the .apj selection, that the source read set matches the retained build manifest and that the saved IA32 `MpTraffic.br` matches the RUC member byte for byte. The scan review reports the library version, type declaration hash, binary hash and event map.

CSV cells may hold an exact integer or its whole-valued decimal form, such as `2.0000`, or the member name. Unknown values and `NONE` fail when a slot records a consumed detection. A recognized assignment or pass-through keeps its own identity and cannot satisfy an arrival. If the project has no verified saved library definition or the RUC differs, the requested check reports an issue. [B&R detection enum](https://help.br-automation.com/#/en/4/motion/mapp_6d/bibliotheken/mptraffic/datatype_constants/mptrafficshdetectionenum.html)

For captures with trusted enum names or a separately verified `eventNames` map, omit `eventEncoding`. Retain the mapping and its version provenance alongside the trace.

## Check the consumed-event sequence in every scan

With br-studio 0.32.0 or later, set `scanCapture.monitorCheckpoints` to true to require four additional columns.

| Destination monitor | Columns |
| --- | --- |
| Inspection leg destination | `inspection.monitorSequenceBeforeScan` and `inspection.monitorSequenceAfterScan` |
| Return leg destination | `return.monitorSequenceBeforeScan` and `return.monitorSequenceAfterScan` |

Use the same monotonic application sequence as the detection slots. Record the counter before consuming this scan's detections and again after the final consumption in that task scan. Increment once for each actual consumed detection before copying its slot. Retain the original sequence on an observation that repeats. Keep each monitor's sequence independent. A scan that consumed no detections has equal before and after counters.

The first inspection row begins after the old queue has been drained and before Release. The first return row witnesses the returned shuttle occupying the return source before the return arrival. The tool uses each first row's before value for its leg baseline. The corresponding JSON baseline fields can be omitted in this mode. When supplied, they must match the captured values.

For each scan the checker compares the before value with the running sequence, walks the ordered slots, then compares the after value with the result. A mismatch leaves the cycle incomplete, including when an unrecorded consumption occurs on the final scan. Keep monitor health and queue occupancy evidence available to assess loss before the application consumed an event. [B&R station monitor behavior](https://help.br-automation.com/#/en/4/motion/mapp_6d/bibliotheken/mptraffic/function_block/mptrafficstationmonitor/fb_mptrafficstationmonitor_functiondescription.html)

## Check the release total across both legs

When `releaseTotalEvent` is set on the cycle request, use `hold.releaseTotal` for the same sampled counter from the start of the outbound leg through the first next-hold scan. If the export uses a different header or chart title, map it through `scanCapture.columns`. Use the value published to the HMI when pairing with a status report. [Event selection and review](cycle-trace-review.md#check-when-the-release-total-increments)

## Review the output

Run `br-studio run --request <request.json>` and retain the response with the descriptor, scan file and build receipts. Missing boundary scans or an internal gap leave the cycle incomplete. Duplicate or decreasing scan indexes, a changed scan file, malformed required values or unaligned chart signals produce issues. The existing cycle reviewer still checks request identity, event continuity, return and the next hold. Compare with a same-session HMI report when validating the page reopen. If `arsim_status_smoke` retained the file in that runtime, point `scanCsv` to the retained copy and set `requireSessionScanCapture` to require the receipt. [Same-run scan retention](arsim-status-smoke.md#retain-a-scan-file-from-the-same-runtime)
