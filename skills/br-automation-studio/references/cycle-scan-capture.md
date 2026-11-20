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
2. Keep the existing `inspectionLeg` and `returnLeg` headers with their captured baseline sequences. Use the original `captureKind`, source .lpj hash and compiled RUC hash from those receipts. Leave out `inspectionLeg.scans`, `returnLeg.scans` and `nextHold` when using a scan file.
3. Add the `scanCapture` object below to the cycle JSON. Hash the exact file with `(Get-FileHash -LiteralPath <capture.csv> -Algorithm SHA256).Hash.ToLowerInvariant()`. Choose the inclusive inspection and return windows and the hold scan from the original capture.

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
| Return source | `return.sourceHealthy`, `return.occupyingShuttleId`, `return.monitorHealthy`, `return.error`, `return.statusId` |
| Destination monitors | Each of `inspection.detection` and `return.detection` needs `.sequence`, `.station`, `.shuttleId`, `.event`. |
| Next held request | `hold.station`, `hold.requestId`, `hold.shuttleId`, `hold.held`, `hold.release`, `hold.resultAccepted` |
| HMI pairing | Include all of `hold.phase`, `hold.releaseTotal` and `hold.heartbeat` when supplying `hmiStatusReport`. Keep the cycle JSON's [`hmi` object](cycle-trace-review.md#match-the-hmi-reopen-to-that-hold). |

The first monitor slot uses `.detection`. For multiple consumed events in one scan, declare the recorded slot counts in `detectionSlots`, for example `{"inspection": 2, "return": 1}`. Add the same four fields for each additional slot using `.detection2`, `.detection3` and subsequent suffixes. Keep the event order from the capture and set unused slot sequences to `0`.

Use `0` for the absent selected or occupying shuttle and for a slot with no observed monitor event. Boolean cells accept `TRUE`, `FALSE` or numeric 0/1. On an observed event, use the sequence, station, shuttle and enum preserved by the capture. If the export stores numeric station or enum values, add `stationNames` and `eventNames` to `scanCapture` with exact raw text keys and the imported station names or version-matched enum names as values. For example, `{"1.0000": "Inspect"}` maps a recorded station code once the capture defines that correspondence.

Read only the window-specific columns for each selected phase. A fault needs its raw `statusId`. The result details are read when `resultPresent` is true. The detection details are read when the event sequence is positive. All columns still need headers so missing signal definitions surface before review.

## Review the output

Run `br-studio run --request <request.json>` and retain the response with the descriptor, scan file and build receipts. Missing boundary scans or an internal gap leave the cycle incomplete. Duplicate or decreasing scan indexes, a changed scan file, malformed required values or unaligned chart signals produce issues. The existing cycle reviewer still checks request identity, event continuity, return and the next hold. Compare with a same-session HMI report when validating the page reopen. If `arsim_status_smoke` retained the file in that runtime, point `scanCsv` to the retained copy and set `requireSessionScanCapture` to require the receipt. [Same-run scan retention](arsim-status-smoke.md#retain-a-scan-file-from-the-same-runtime)
