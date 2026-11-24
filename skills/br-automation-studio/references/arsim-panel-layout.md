# Rehearse the Assembly6D panel layout

Use this workflow with br-studio 0.116.0 or later on a trusted, source-matched AS4 PC simulation build. It checks the PLC counter binding, selected Assembly6D widget, startup content, widget bundle, and Traffic Manager labels and one-way mapping lines in one ARsim session. Both browser documents use the chosen panel viewport. [Combined HMI workflow](arsim-hmi-smoke.md)

## Run the panel check

Save this example as `panel-smoke.json`. Select the actual project and retained build directory, installed PVI and Chrome, and a new session directory. Supply the saved default visualization, content and numeric counter IDs, the one-way PLC binding identifier, and the generated Assembly6D DOM ID. Set the width and height to the browser content area in CSS pixels. Replace the three example label limits with the project values.

```json
{
  "schemaVersion": 1,
  "workspace": "panel-layout",
  "operation": "arsim_hmi_smoke",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "jobDirectory": "C:/BuildJobs/example-arsim-001",
    "pviTransfer": "C:/Program Files/BRAutomation4/PVI/V4.12/PVI/Tools/PVITransfer/PVITransfer.exe",
    "browserExecutable": "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "sessionDirectory": "C:/BuildJobs/example-panel-001",
    "visualizationId": "CounterPanel",
    "contentId": "Overview",
    "widgetId": "CountValue",
    "stringIdentifier": "::Pulse:Count",
    "assemblyWidgetId": "Overview_PlanarOverview",
    "viewportWidthCssPx": 1280,
    "viewportHeightCssPx": 800,
    "requireTrafficLabelsInBounds": true,
    "requireTrafficMappingLines": true,
    "requireNonOverlappingTrafficLabels": true,
    "minimumTrafficLabelHeightCssPx": 5,
    "minimumTrafficLabelGapCssPx": 30,
    "minimumTrafficLabelEdgeClearanceCssPx": 40,
    "requireWidgetBundle": true,
    "captureLayoutAssets": true,
    "timeoutSeconds": 55,
    "browserTimeoutSeconds": 65,
    "opcUaTimeoutSeconds": 25
  }
}
```

Run `br-studio run --request panel-smoke.json` and retain the JSON. Review the saved and compiled setting under `result.rucReview.startPageWidget.trafficDetails`. The fields below are relative to `result`.

| Evidence | Readback |
| --- | --- |
| Build and PLC counter | `arsimSmoke.packageSha256`, `bindingReview`, and `arsimSmoke.hmi.comparison` identify the build, binding and direct read bounds around the browser samples. |
| Each browser document | `arsimSmoke.hmi.browser.initial` and `arsimSmoke.hmi.browser.reopened` record the measured viewport, Assembly6D bounds, startup map and content, widget bundle, layout requests, label measurements and observed mapping lines. |
| Generated assets and cleanup | `arsimSmoke.hmi.browser.layoutCapture.assets` gives the two SVG paths and hashes. Keep the JSON with the recorded session's `session.json` and `layout-assets/` directory. Check the Chrome and ARsim cleanup fields. |

When comparing two viewport sizes, use the same build, widget, binding and limits. Use a fresh session directory for each run. Compare the measured viewport, widget bounds and label evidence by browser document. If a run stops before reopening, read the first document and the diagnostic. Capture follows a successful browser check.

## Review the captured layout

Save the completed HMI JSON beside `session.json` and `layout-assets/` in the session folder. Move all three together when archiving a run. Save this example as `panel-geometry.json`. Use the same saved project and retained build, the linked Traffic Manager ID, and the completed HMI report.

```json
{
  "schemaVersion": 1,
  "workspace": "panel-layout",
  "operation": "layout_svg_review",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "jobDirectory": "C:/BuildJobs/example-arsim-001",
    "managerId": "PanelTraffic",
    "hmiReport": "C:/BuildJobs/example-panel-001/panel-smoke-report.json",
    "verifyMappingLineEndpoints": true,
    "verifyTrafficLabels": true
  }
}
```

Run `br-studio run --request panel-geometry.json`. The review verifies the build, widget, session and captured SVG hashes before comparing layout geometry. Check `layoutSvgReview.hmiCapture`, the mapping endpoints and the label anchors. Use the intended panel and the version-matched project for the visual and live Traffic Manager acceptance checks. [Generated layout workflow](layout-svg-review.md#read-the-svgs-beside-the-combined-hmi-response)
