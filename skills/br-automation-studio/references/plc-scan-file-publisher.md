# Publish a PLC scan file in AS4 ARsim

Use the AS4 `FileIO` library to publish a finalized CSV from the simulation task that owns the capture. The producer closes the temporary file before renaming it to the basename that `arsim_status_smoke` waits for. Use a fresh ARsim session directory for each run. [Retain a scan from ARsim](arsim-status-smoke.md#retain-a-scan-file-from-the-same-runtime)

## Set up the file device and buffer

In the private PC simulation configuration, define the file device `CaptureData` with path `USER_PATH`. Verify that the generated file appears in `runtime/USER` in the ARsim session. Register `FileIO` in the saved project. The example also uses `AsBrStr` for the byte count of its ASCII string buffer.

Capture the actual task scan index, heartbeat and application values in one row per scan. Wait until the last required scan has been recorded, freeze the complete buffer, and set `CaptureStep := 10` once. Keep the buffer unchanged until the publisher finishes. Size the buffer for the header, records, separators, line endings and terminating NUL. Check bounds before appending each row. Use the cycle capture column contract for a full inspection cycle. [Cycle scan columns](cycle-scan-capture.md)

## Publish the closed file

Add the following declarations to the capture program. In this example, `CaptureStep = 50` indicates publication. `CaptureStep = 90` indicates a terminal file error and `CaptureError` holds the first nonzero FileIO status. Keep each FB active while its status is busy. FileCreate returns the open handle, so pass that handle directly to FileWrite and FileClose. [FileCreate](https://help.br-automation.com/#/en/4/libraries/fileio/fbks/filecreate.html) · [FileWrite](https://help.br-automation.com/#/en/4/libraries/fileio/fbks/filewrite.html) · [FileClose](https://help.br-automation.com/#/en/4/libraries/fileio/fbks/fileclose.html) · [FileRename](https://help.br-automation.com/#/en/4/libraries/fileio/fbks/filerename.html)

```iecst
VAR
    CaptureStep : USINT;
    CaptureError : UINT;
    CaptureFileId : UDINT;
    CaptureDevice : STRING[15] := 'CaptureData';
    CaptureTempName : STRING[63] := 'held-status.partial';
    CaptureFinalName : STRING[63] := 'held-status.csv';
    CaptureCsv : STRING[8191];
    CaptureCreate : FileCreate;
    CaptureWrite : FileWrite;
    CaptureClose : FileClose;
    CaptureRename : FileRename;
END_VAR
```

Execute these state cases in the cyclic program after the capture logic. A write error still goes through FileClose and leaves the temporary name for diagnosis. A successful close enables FileRename on the same file device.

```iecst
CASE CaptureStep OF
    10:
        CaptureCreate(enable := TRUE, pDevice := ADR(CaptureDevice), pFile := ADR(CaptureTempName));
        IF CaptureCreate.status = 0 THEN
            CaptureFileId := CaptureCreate.ident;
            CaptureStep := 20;
        ELSIF CaptureCreate.status <> 65535 THEN
            CaptureError := CaptureCreate.status;
            CaptureStep := 90;
        END_IF;

    20:
        CaptureWrite(enable := TRUE, ident := CaptureFileId, offset := 0,
                     pSrc := ADR(CaptureCsv), len := brsstrlen(ADR(CaptureCsv)));
        IF CaptureWrite.status <> 65535 THEN
            CaptureError := CaptureWrite.status;
            CaptureStep := 30;
        END_IF;

    30:
        CaptureClose(enable := TRUE, ident := CaptureFileId);
        IF CaptureClose.status = 0 THEN
            IF CaptureError = 0 THEN
                CaptureStep := 40;
            ELSE
                CaptureStep := 90;
            END_IF;
        ELSIF CaptureClose.status <> 65535 THEN
            IF CaptureError = 0 THEN
                CaptureError := CaptureClose.status;
            END_IF;
            CaptureStep := 90;
        END_IF;

    40:
        CaptureRename(enable := TRUE, pDevice := ADR(CaptureDevice),
                      pName := ADR(CaptureTempName), pNewName := ADR(CaptureFinalName));
        IF CaptureRename.status = 0 THEN
            CaptureStep := 50;
        ELSIF CaptureRename.status <> 65535 THEN
            CaptureError := CaptureRename.status;
            CaptureStep := 90;
        END_IF;
END_CASE;
```

## Check the retained capture

In the status request, set `scanCaptureFile` to `held-status.csv`. Select a timeout that allows the final acquisition and the asynchronous write, close and rename to finish. Keep the status response, original `session.json` and retained `scan-capture.csv` together. Check the file SHA-256 and verify that the final held heartbeat reaches the last direct read after the HMI reopens. Use the full-cycle descriptor and actual station observations for cycle acceptance. [First-cycle review](inspection-cycle-rehearsal.md)

This publisher was compiled on AS4.12 with Automation Runtime J4.93 and exercised with a synthetic held station. The PLC published 150 consecutive heartbeat records. The retained 2,441-byte CSV matched the file in `runtime/USER` and reached heartbeat 194. The final direct read after reopen was 114.

## Review the held window

With br-studio 0.119.0 or later, set the four role labels in the status request's binding file. Use `requestId`, `shuttleId`, `phase` and `releaseTotal`. Keep the completed response beside the original `session.json` and `scan-capture.csv`. Save the following as `hold-review.json`. [Role-labeled status bindings](arsim-status-smoke.md#select-the-expected-status)

```json
{
  "schemaVersion": 1,
  "workspace": "held-window",
  "operation": "held_status_scan_review",
  "arguments": {
    "project": "project/Example.apj",
    "jobDirectory": "build",
    "hmiStatusReport": "session/status-response.json"
  }
}
```

Use a UTF-8 comma-delimited CSV with distinct headers `scan`, `heartbeat`, `requestId`, `shuttleId`, `phase` and `releaseTotal`. Capture a consecutive scan window during which all four values are held. Record a nondecreasing heartbeat and include each value observed in both browser documents and both direct OPC UA windows.

An optional `scanDescriptor` points to a JSON file for renamed columns or older status reports. `columns` maps a field to its CSV header. `bindings` maps all four held-status roles to their reviewed OPC UA string identifiers when supplying an explicit map. For a role-labeled receipt, omit `bindings` and use the captured roles.

```json
{
  "schemaVersion": 1,
  "columns": {
    "heartbeat": "PlcHeartbeat"
  },
  "bindings": {
    "requestId": "::Status:RequestId",
    "shuttleId": "::Status:ShuttleId",
    "phase": "::Status:Phase",
    "releaseTotal": "::Status:ReleaseTotal"
  }
}
```

Run `br-studio run --request hold-review.json`. The review verifies the retained build, matches the runtime JSON and CSV hash, and compares the scan values with the four status bindings. Read `heldStatusScanReview.window` for the scan range and any missing heartbeat witnesses. For release, arrival and return acceptance, run the full cycle review with the corresponding scan columns. [Inspection-cycle rehearsal](inspection-cycle-rehearsal.md)

### Select the hold from a complete cycle CSV

With br-studio 0.120.0 or later, set `"cycleTrace": "cycle-trace.json"` in the review request in place of `scanDescriptor`. Keep the complete original `scan-capture.csv` beside the status response. Use the same cycle descriptor you pass as `trace` to `cycle_trace_review`. It supplies the CSV delimiter, column and station mappings, and any explicit HMI bindings. The review matches any supplied scan hash, session ID and heartbeat identifier to the archived evidence. [Full-cycle descriptor](inspection-cycle-rehearsal.md#review-the-saved-svgs-and-plc-scans)

The reviewer selects every scan from `scanCapture.nextHoldScan` through `holdThroughScan`, inclusive. Use `"last"` when the file ends during the hold. Through that range, keep the station and status values steady, hold active, and Release and result acceptance clear. The selected scans must include every heartbeat sampled by the HMI and direct OPC UA. Read `cycleSelection` for the requested bounds and `window` for the checked scan range and station.
