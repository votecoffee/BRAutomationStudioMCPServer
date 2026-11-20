# Check the compiled mapp View HMI in one ARsim session

Use `arsim_hmi_smoke` for a design review of a trusted AS4 PC simulation RUC. It verifies the saved and compiled counter binding and the default visualization, then compares the browser display with direct OPC UA readings in one ARsim process job.

## Select the HMI witness

Use an increasing PLC integer with a local, one-way OPC UA value binding. The numeric widget should display that integer without scaling or wrapping during the check. Use zero or blank while the browser widget is unbound. The selected visualization must be the default in the saved and packaged mapp View server configuration. Its opening page must show the numeric widget and the reviewed `Assembly6D` widget.

Install the two runtime extras in the environment that runs `br-studio`.

```powershell
.\.local\venv\Scripts\python.exe -m pip install ".[browser,opcua]"
```

Use `br-studio operation-help arsim_hmi_smoke` to discover the request contract. Select the saved [binding IDs](ruc-binding-review.md) and the intended PLC identifier. The operation derives the numeric widget's DOM ID and OPC UA namespace from that binding. If the 6D review contains more than one Assembly6D widget, supply the full DOM ID in `assemblyWidgetId`.

```json
{
  "schemaVersion": 1,
  "workspace": "loopback-hmi",
  "operation": "arsim_hmi_smoke",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "jobDirectory": "C:/BuildJobs/example-arsim-001",
    "pviTransfer": "C:/Program Files/BRAutomation4/PVI/V4.12/PVI/Tools/PVITransfer/PVITransfer.exe",
    "sessionDirectory": "C:/BuildJobs/example-hmi-001",
    "browserExecutable": "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "visualizationId": "CounterPanel",
    "contentId": "Overview",
    "widgetId": "CountValue",
    "stringIdentifier": "::Pulse:Count",
    "browserTimeoutSeconds": 40
  }
}
```

Run `br-studio run --request <hmi-request.json>`. Use `--format markdown` for the readable report. Give every launch a new session directory. The runtime uses the [loopback fixture prerequisites](arsim-smoke.md). The OPC UA timeout applies separately to each direct read window.

## Read the evidence

1. Confirm the binding, compiled default visualization, 6D configuration and rendered Assembly6D links belong to the same completed build. A binding or default visualization mismatch stops before runtime materialization.
2. The loopback HTTP check matches the served index, selected counter and Assembly6D content, and widget script against the pinned package.
3. The first anonymous OPC UA session reads three advancing values and checks the server-reported access fields. Chrome reads the counter and renders Assembly6D before and after reopening the HMI in a fresh document. A second direct session reads the same node on the same owned ARsim listener. The two direct readings must report the integer type found in the compiled map. [OPC UA built-in types](https://reference.opcfoundation.org/specs/OPC-10000-6/5.1.2)
4. Every accepted browser value must be a whole number between the last direct value before Chrome and the first direct value afterward, inclusive. The readings come from ordered sampling windows in the same runtime. Use the bounds as a consistency check and the saved-to-compiled binding to establish the node identity.
5. Check `result.evidence` for `arsim_loopback_hmi`. Keep `session.json` and the report with the binding paths, probe phases, values and cleanup. Verify the two OPC UA sessions disconnected and the Chrome and ARsim jobs were cleared. For a failure, follow its phase to the [binding](ruc-binding-review.md), [direct OPC UA](arsim-opcua-smoke.md) or [browser](arsim-browser-smoke.md) workflow.

This check covers a read-only status path and a browser page reopen. Simulator shuttle movement, control commands, and production acceptance each require their own evidence.
