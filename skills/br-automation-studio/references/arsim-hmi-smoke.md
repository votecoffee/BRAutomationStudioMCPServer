# Check the compiled mapp View HMI in one ARsim session

Use `arsim_hmi_smoke` for a design review of a trusted AS4 PC simulation RUC. It verifies the saved and compiled counter binding and the default visualization, then compares the browser display with direct OPC UA readings in one ARsim process job.

Use the [status reconnect workflow](arsim-status-smoke.md) to verify held request, shuttle and phase values around the same browser reopen.

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

The browser report includes the expected `6DData` SVG requests and an inline SVG count for each observed HMI document. Set `requireLayoutAssets` to `true` to require HTTP 200 or 304 for the assembly SVG and the linked manager SVG (when configured), plus an inline SVG with nonzero layout bounds inside the widget. The counter and SVG checks use the same document window. The SVG result covers the completed requests and DOM bounds. Visual accuracy and live shuttle motion require target evidence.

Set `requireStartPage` to `true` to verify the selected Assembly6D widget's default page assignment and the generated client map from the same runtime. [Default start page check](arsim-browser-smoke.md#verify-the-default-start-page-in-arsim)

For a build planned with `launchPadWidgetId`, the browser uses the pinned widget by default and checks its served client map in both documents. When supplying `assemblyWidgetId`, use the same ID. [Plan the widget check](native-build.md#check-the-default-assembly6d-widget)

Set `requireWidgetBundle` to `true` to verify the packaged Assembly6D bundle, the served bytes and the script load in each browser document. The same option works with a [status smoke](arsim-status-smoke.md) when an Assembly6D widget is available. [Widget bundle check](arsim-browser-smoke.md#verify-the-assembly6d-bundle-in-both-documents)

Set `captureLayoutAssets` to `true` to enable that browser requirement and save the expected SVGs from the same owned ARsim HTTP listener. Look for `hmi.browser.layoutCapture` in `session.json`. Its paths are relative to the session directory. Retain those files and the receipt for the offline review. [SVG capture and readback](arsim-browser-smoke.md#save-the-generated-svgs)

### Check the Traffic Manager display option alongside the layout

With br-studio 0.106.0 or later, set `requireTrafficDetails` and `captureLayoutAssets` to `true` in the same `arsim_hmi_smoke` request. The first option checks the saved and compiled display setting and the selected start page content in each browser document. The second requires both browser layout observations and saves the generated SVGs. The direct OPC UA readings still bracket the browser counter values. [Start page content check](arsim-browser-smoke.md#follow-the-traffic-manager-details-option-into-the-start-page)

If both `6DData` requests return HTTP 404, run `layout_device_review` with the same project and `jobDirectory`. Confirm that the selected CPU and compiled RUC declare the file device and agree on its path. If the declarations agree, check the runtime directory and the assembly's generation diagnostics. [File-device workflow](layout-device-review.md)

Use the two captured SVGs with `layout_svg_review` on the same job to check the traffic labels and route geometry. Verify changing Traffic Manager details on the target while its manager is running. [Generated layout workflow](layout-svg-review.md)

Set `requireTrafficLabels` to `true` in br-studio 0.107.0 or later to require the compiled station and waypoint names to appear as visible SVG text inside the Assembly6D widget in each browser document. This includes `requireTrafficDetails` and `requireLayoutAssets`. Set `captureLayoutAssets` separately when you also need the SVG files. [Browser label check](arsim-browser-smoke.md#verify-the-station-and-waypoint-labels-in-chrome)

Set `minimumTrafficLabelHeightCssPx` to enable the label check and enforce a project-selected minimum in both browser documents. The same option works with `arsim_status_smoke` and `arsim_browser_smoke`. [Label size evidence](arsim-browser-smoke.md#check-label-size)

Set `viewportWidthCssPx` and `viewportHeightCssPx` together to run the check at the chosen content area. Both documents record the measured dimensions before accepting counter samples. [Viewport workflow](arsim-browser-smoke.md#set-the-browser-viewport)

Set `requireTrafficLabelsInBounds` to require every verified label to fit in both the widget and the viewport content area. This also works in `arsim_status_smoke` and `arsim_browser_smoke`. [Label bounds workflow](arsim-browser-smoke.md#check-that-labels-fit-in-view)

## Read the evidence

1. Confirm the binding, compiled default visualization, 6D configuration and rendered Assembly6D links belong to the same completed build. A binding or default visualization mismatch stops before runtime materialization.
2. The loopback HTTP check matches the served index, selected counter and Assembly6D content, and widget script against the pinned package.
3. The first anonymous OPC UA session reads three advancing values and checks the server-reported access fields. Chrome reads the counter and renders Assembly6D before and after reopening the HMI in a fresh document. A second direct session reads the same node on the same owned ARsim listener. The two direct readings must report the integer type found in the compiled map. [OPC UA built-in types](https://reference.opcfoundation.org/specs/OPC-10000-6/5.1.2)
4. Every accepted browser value must be a whole number between the last direct value before Chrome and the first direct value afterward, inclusive. The readings come from ordered sampling windows in the same runtime. Use the bounds as a consistency check and the saved-to-compiled binding to establish the node identity.
5. Check `result.evidence` for `arsim_loopback_hmi`. Keep `session.json` and the report with the binding paths, probe phases, values and cleanup. Verify the two OPC UA sessions disconnected and the Chrome and ARsim jobs were cleared. For a failure, follow its phase to the [binding](ruc-binding-review.md), [direct OPC UA](arsim-opcua-smoke.md) or [browser](arsim-browser-smoke.md) workflow.

This check covers a read-only status path and a browser page reopen. Simulator shuttle movement, control commands, and production acceptance each require their own evidence.
