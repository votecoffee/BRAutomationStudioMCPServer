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

## Interpret the result

- `matched` requires one Release edge with the approved request and shuttle, route permission, station clear and inspection busy clear. The same leg needs ReleaseDone and a fresh `mpTRAFFIC_SH_ARRIVAL` at the expected station for that shuttle. Either observation may appear first in the PLC trace after release.
- Read `bufferedScan` separately. A missing Buffered observation is recorded as null. An Abort or Error takes priority over Done or arrival on the same scan. The report retains the first raw adapter status.
- `incomplete` means a required observation or continuity is missing. A stale sequence, wrong shuttle, pass-through or manual assignment cannot satisfy arrival. `issues` identifies conflicts such as a second Release edge, a changed request, invalid trace identity or adapter fault.
- A matched report establishes consistency within the captured leg. Collect the separate return leg, next held request and HMI reconnect evidence for a complete inspection cycle.
