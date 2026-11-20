# Verify held PLC status after reopening mapp View

Use `arsim_status_smoke` on a trusted AS4 PC simulation RUC. This checks one-way numeric status bindings and an advancing heartbeat in two browser documents and in one owned ARsim runtime. For an inspection hold, expose the request ID, selected shuttle ID, phase and release count as read-only values.

## Select the expected status

Start with a [combined HMI request](arsim-hmi-smoke.md#select-the-hmi-witness) for the heartbeat widget. Change the operation to `arsim_status_smoke` and add `statusBindingFile` to the arguments. Its path resolves from the request directory. Use the same visualization and content for all the selected widgets.

```json
{
  "schemaVersion": 1,
  "bindings": [
    {"widgetId": "RequestValue", "stringIdentifier": "::Status:RequestId", "expectedValue": 17},
    {"widgetId": "ShuttleValue", "stringIdentifier": "::Status:ShuttleId", "expectedValue": 7},
    {"widgetId": "PhaseValue", "stringIdentifier": "::Status:Phase", "expectedValue": 10},
    {"widgetId": "ReleaseValue", "stringIdentifier": "::Status:ReleaseTotal", "expectedValue": 3}
  ]
}
```

Use separate widget IDs and node identifiers for the heartbeat and each status. Select a held condition that persists across both read windows. Status values use scalar integers and one-way default OPC UA bindings on the same loopback endpoint. Choose distinguishable display values so that the expected fields can be seen after the widget loads.

Install the `browser` and `opcua` extras. Run `br-studio operation-help arsim_status_smoke` to see the request fields, then run `br-studio run --request <request.json>`. Use a fresh session directory for each run.

To include Assembly6D in this check, supply the reviewed `assemblyWidgetId` or request the layout check with `requireLayoutAssets` or `captureLayoutAssets`. Leaving those fields unset runs the numeric status and heartbeat checks.

## Retain a scan file from the same runtime

Since br-studio 0.31.0, set `scanCaptureFile` to a finalized `.csv` basename such as `inspection-cycle.csv` when the simulation project can export its scan buffer. Have the project publish the final name in its ARsim `USER` file device after the next hold. Close the temporary output before publishing the final name, and publish it once per run. The smoke probe checks that the final name is absent after materialization and before starting ARsim.

After both browser documents and direct OPC UA reads pass, the tool waits up to `scanCaptureTimeoutSeconds` for the final file. The default is 20 seconds. It checks that the original owned listener is still serving, reads at most 16 MiB from the finalized file, and saves the exact bytes as `scan-capture.csv` next to `session.json`. The status smoke reports an issue if the expected file is missing or cannot be retained. A file retained during the session binds its bytes to the smoke report. Keep the project's acquisition record to identify the producer.

Retain the response, `session.json` and captured file together. In `cycle_trace_review`, use the retained file as `scanCsv`, copy the same SHA-256 into the cycle descriptor and set `requireSessionScanCapture` to true. The review compares that file with the status receipt's hash and byte length even when the retained copy has been moved. [Scan file descriptor and review](cycle-scan-capture.md#bind-the-scan-file)

## Read the evidence

- Check the saved and compiled binding reviews for each numeric widget. A mismatch stops before ARsim starts.
- In `hmi.opcUaBefore.statusValues` and `hmi.opcUaAfter.statusValues`, verify the expected node values, Good quality, compiled scalar type, and CurrentRead access for the variable and session. The initial read waits for the expected hold within the OPC UA timeout. Both read windows check the status before and after sampling the heartbeat on the same owned OPC UA listener. [OPC UA built-in types](https://reference.opcfoundation.org/specs/OPC-10000-6/5.1.2)
- Read `hmi.browser.initial.statusValues` and `hmi.browser.reopened.statusValues`. Each document must show the expected integers while the heartbeat advances. The reopened document needs a distinct time origin and newer heartbeat samples. Check the before and after OPC UA readings around those browser samples.
- Retain the report and `session.json` showing `arsim_loopback_status` evidence and empty Chrome and ARsim jobs. Since br-studio 0.29.0, the response and `session.json` include the same `sessionId`. Record it with the PLC scan capture and use the [paired cycle review](cycle-trace-review.md#match-the-hmi-reopen-to-that-hold) to compare the returned shuttle's next hold with this browser reopen.
