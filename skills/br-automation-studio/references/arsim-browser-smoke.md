# Verify a loopback mapp View HMI in Chrome

Use this workflow on a trusted AS4 PC simulation fixture with a reviewed RUC. It runs the [ARsim HTTP smoke](arsim-smoke.md), opens the same running mapp View HMI in headless Chrome, and reads a selected Assembly6D widget and a changing numeric display before and after reopening the page.

## Select the HMI witnesses

Follow the loopback fixture requirements in the HTTP workflow. In the initial HMI page, select a numeric widget with a read-only binding to a PLC counter that starts at zero and increases during the test. The operation reads the rendered text or input value, so select a widget that displays plain digits with an optional decimal point. Disable thousands separators and use zero or blank for the unbound display.

Use the generated page DOM IDs. For AS4 generated content, these take the form `ContentId_WidgetId`. Check the reviewed RUC report for the Assembly6D content and widget IDs. When it lists more than one Assembly6D widget, supply the full ID of the one rendered by the initial page. Supply the numeric widget's full ID in every request.

Install the CLI with the browser extra in the same environment that runs `br-studio`. From a checkout, use the following command.

```powershell
.\.local\venv\Scripts\python.exe -m pip install ".[browser]"
```

Use `br-studio operation-help arsim_browser_smoke` to discover the contract. Select an installed Windows Google Chrome executable with headless support.

```json
{
  "schemaVersion": 1,
  "workspace": "loopback-hmi",
  "operation": "arsim_browser_smoke",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "jobDirectory": "C:/BuildJobs/example-arsim-001",
    "pviTransfer": "C:/Program Files/BRAutomation4/PVI/V4.12/PVI/Tools/PVITransfer/PVITransfer.exe",
    "sessionDirectory": "C:/BuildJobs/example-browser-001",
    "browserExecutable": "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "counterWidgetId": "Overview_CountValue",
    "assemblyWidgetId": "Overview_PlanarOverview",
    "timeoutSeconds": 30,
    "browserTimeoutSeconds": 40
  }
}
```

Run `br-studio run --request <browser-request.json>`. Set `timeoutSeconds` for the ARsim HTTP startup window and `browserTimeoutSeconds` for the complete Chrome probe. Select `--format markdown` on the same invocation if you need the readable format. Use a different session directory for each invocation.

## Set the browser viewport

In br-studio 0.109.0 or later, supply both `viewportWidthCssPx` and `viewportHeightCssPx` to rehearse at a chosen viewport size. Chrome applies the desktop dimensions before opening the HMI. Each document must report the requested width and height before the browser probe accepts samples. The receipt records `requestedViewport` and the measured `viewport` with its device pixel ratio under both `initial` and `reopened`. [Chrome DevTools device metrics](https://chromedevtools.github.io/devtools-protocol/tot/Emulation/#method-setDeviceMetricsOverride)

Use the panel browser's content area in CSS pixels when selecting the dimensions. Combine this setting with the [label size check](#check-label-size) to compare the actual SVG label bounds at that viewport. These dimensions also work with `arsim_hmi_smoke` and `arsim_status_smoke`.

## Verify the default start page in ARsim

Set `requireStartPage` to `true` in br-studio 0.40.0 or later to check that the selected Assembly6D widget is assigned to the default visualization's start page. The operation uses the [saved and compiled placement review](ruc-review.md#verify-the-default-page-placement-of-assembly6d) on the selected build before starting ARsim. Supply `assemblyWidgetId` when several Assembly6D widgets exist.

The owned ARsim listener must return the exact packaged visualization client map at the lowercase URL used by the AS4 browser. For example, the RUC member `ExampleVisu.json` corresponds to `/examplevisu.json`. The HTTP record identifies the archive member and the response SHA-256. Both browser documents must record a same-origin XHR or fetch for that map with HTTP 200 or 304. See `startPageMap` under `initial` and `reopened` in the browser receipt.

### Follow the Traffic Manager details option into the start page

Set `requireTrafficDetails` to `true` in br-studio 0.105.0 or later to include the start page check and require the selected Assembly6D widget's [saved and compiled Traffic Manager display option](ruc-review.md#verify-the-generated-traffic-manager-display-option). Review `rucReview.startPageWidget.trafficDetails` for both Boolean values before interpreting the browser evidence.

The ARsim HTTP receipt gives the selected content's RUC member and the matching response SHA-256. Both browser documents must show a same-origin XHR or fetch with HTTP 200 or 304 for that content's exact URL from the generated client map. Check `startPageContent` alongside `startPageMap` in the `initial` and `reopened` records. An unavailable status leaves the load unverified. [W3C Resource Timing](https://www.w3.org/TR/resource-timing/)

This check covers loading the content that carries the option while the selected Assembly6D widget and advancing counter are present. Verify the actual detail values with an active Traffic Manager on the intended runtime.

## Verify the Assembly6D bundle in both documents

Set `requireWidgetBundle` to `true` in br-studio 0.38.0 or later to require the AS4 release bundle. The CLI finds `BRVisu/release/brease.js` in the reviewed RUC, checks for one named `widgets/mapp6D/Assembly6D/Assembly6D` AMD declaration, and compares the bytes served by the owned HTTP listener with that RUC member. The HTTP receipt records the archive path, module name and SHA-256.

Each browser document must also show a same-origin script load for `/BRVisu/release/brease.js` with HTTP 200 or 304. See `widgetBundle` in both the `initial` and `reopened` document records. The browser status can reflect revalidation of its cache. The separate HTTP check supplies the byte comparison. The evidence covers the AS4 release bundle and document loads. Use the layout and counter checks for the rendered widget and PLC updates. [W3C Resource Timing](https://www.w3.org/TR/resource-timing/)

## Check the layout assets

Use the [file-device review](layout-device-review.md) to verify the selected CPU's saved and compiled `6DData` declaration.

The browser report includes the expected `6DData` SVG requests and an inline SVG count for each observed HMI document. Set `requireLayoutAssets` to `true` to require HTTP 200 or 304 for the assembly SVG and the linked manager SVG (when configured), plus an inline SVG with nonzero layout bounds inside the widget. The counter and SVG checks use the same document window. The SVG result covers the completed requests and DOM bounds. Visual accuracy and live shuttle motion require target evidence.

The expected URLs are derived from the compiled widget links as `/FileDevice:6DData/<symbol>.svg`. The report uses browser resource timing for those exact URLs. An unavailable status or missing inline bounds leaves the layout unverified. An HTTP error is reported as `request_failed`. [B&R layout display troubleshooting](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/faq.html)

### Verify the station and waypoint labels in Chrome

In br-studio 0.107.0 or later, set `requireTrafficLabels` to `true` to include the Traffic Manager display-option and layout checks. The expected labels come from the compiled stations and waypoints of the manager linked to the selected widget and assembly. The same option works in `arsim_hmi_smoke` and `arsim_status_smoke`.

In each browser document, the selected Assembly6D widget's inline Traffic Manager SVG must contain one `label_<name>` text element per expected object. Its text must equal the compiled name, with nonzero layout bounds, visible CSS visibility and nonzero ancestor opacity. Review `trafficLabels` under `initial` and `reopened` for the text, element count and visibility. This evidence covers the AS4 SVG format and DOM layout. Check clipping, readability and changing shuttle or manager state on the intended runtime. [SVG text rendering](https://www.w3.org/TR/SVG2/text.html#Introduction)

#### Check label size

In br-studio 0.108.0 or later, the traffic label receipt also records each text element's bounding width and height, the smallest verified height, and the viewport size and device pixel ratio for that document. The dimensions are CSS pixels from the browser layout, including transforms. [CSSOM View](https://www.w3.org/TR/cssom-view/#dom-element-getboundingclientrect)

In br-studio 0.110.0 or later, read `assemblyBounds` under each browser document to get the widget's rendered width and height in CSS pixels. To measure the effect of changing widget dimensions, compare builds at the same viewport and check the widget bounds alongside the label heights. [Set the browser viewport](#set-the-browser-viewport)

Set `minimumTrafficLabelHeightCssPx` to a positive whole number to turn on the label checks and enforce the project-selected height in both documents. The comparison uses the measured value at full precision. When the label text and visibility pass but the size falls short, look for `below_minimum` and `belowMinimum` in the affected document. Compare receipts at the viewport dimensions intended for the HMI and confirm readability on the panel.

#### Check that labels fit in view

Starting with br-studio 0.111.0, each measured label reports `insideWidget` and `insideViewport`. The probe compares the text rectangle with the Assembly6D widget rectangle and the viewport content area in CSS pixels. `viewportContent` records the browser area available after scrollbars. [CSSOM View geometry](https://www.w3.org/TR/cssom-view/#extensions-to-the-element-interface)

Set `requireTrafficLabelsInBounds` to `true` to enable the label check and require both containment checks in each browser document. Labels extending past either rectangle appear under `outsideWidget` or `outsideViewport`. Combine this option with the chosen [viewport](#set-the-browser-viewport) and label height when needed. Inspect label overlap and readability on the intended panel.

#### Check clearance from the widget and viewport edges

Starting with br-studio 0.114.0, `trafficLabels.edgeClearance` records the smallest distance to a widget edge and the smallest distance to a viewport edge. Each record includes the label and side. A label touching the boundary has zero clearance. An edge extending past the boundary gives a negative distance. The measurements use the same text rectangles as the bounds check. [CSSOM View geometry](https://www.w3.org/TR/cssom-view/#dom-element-getboundingclientrect)

Set `minimumTrafficLabelEdgeClearanceCssPx` to a positive whole number to enable the label check and require that clearance for both areas on both page loads. A measured distance equal to the requested minimum passes. Use the receipt to identify the label and edge being limited.

#### Check for overlapping labels

Starting with br-studio 0.112.0, `trafficLabels.overlap` records the number of labels with measured rectangles and any pairs whose rectangles share positive area. Edges that only touch pass. The check uses the same CSS pixel rectangles for the verified station and waypoint text in each browser document. [CSSOM View geometry](https://www.w3.org/TR/cssom-view/#dom-element-getboundingclientrect)

Set `requireNonOverlappingTrafficLabels` to `true` to enable the label check and require distinct label rectangles on both loads. It can run alongside the minimum height and bounds checks. Review the reported pairs when repositioning station or waypoint labels, and confirm legibility on the panel.

#### Check the gap between labels

Starting with br-studio 0.113.0, `trafficLabels.overlap` also records `minGapCssPx` and `closestPair` for each document. The gap is the shortest distance between the text rectangles, including diagonally separated labels. Touching or overlapping rectangles have a gap of zero. A view with one verified label has no pair. [CSSOM View geometry](https://www.w3.org/TR/cssom-view/#dom-element-getboundingclientrect)

Set `minimumTrafficLabelGapCssPx` to a positive whole number to enable the label check and require the project-selected clearance on both loads. A measured gap equal to the requested minimum passes. The receipt records the closest pair and the full-precision measurement.

### Verify one-way mapping lines in Chrome

With br-studio 0.116.0 or later, set `requireTrafficMappingLines` in the browser, combined HMI or status request. It requires the enabled Traffic Manager display option, the linked SVGs and the compiled one-way mapping IDs. In both documents, the selected Assembly6D widget must contain one matching Traffic Manager group with a visible, nonzero SVG line for each mapping. Check `initial.trafficMappings` and `reopened.trafficMappings` under the browser receipt for group counts, line bounds and unverified IDs.

Use the archived SVG geometry review to check the mapping endpoints. Inspect clipping and route appearance on the intended panel. [Panel layout rehearsal](arsim-panel-layout.md)

### Save the generated SVGs

Set `captureLayoutAssets` to `true` in the request arguments. This also enables the required layout check in both browser documents. After the browser closes, the CLI makes a fresh GET for each expected URL on the same owned ARsim HTTP listener. Each response must be HTTP 200 with an `image/svg+xml` body and a namespaced SVG root. The browser status and capture refer to separate requests inside the same ARsim session.

Keep `session.json` and the `layout-assets/` directory together. The `layoutCapture` section records the listener and each URL, local path, byte count, media type and SHA-256. Its `assembly` and `trafficManager` records identify the files to use for `assemblySvg` and `trafficSvg` in the [generated layout review](layout-svg-review.md). A widget with only an assembly link yields a single assembly SVG.

## Interpret the receipts

- Check for `arsim_loopback_browser` in `result.evidence` and `complete` in `result.arsimSmoke.browser.state`. The receipt includes the pinned RUC and HTTP asset checks, Chrome version, owned debugger endpoint, widget IDs, and the numeric samples.
- The initial document needs the selected Assembly6D widget and three increasing positive values. The probe confirms an intervening blank document, then requires a separate HMI document with the same widget type and two increasing values above the last sample from the first document. Zero placeholders and repeated values are excluded. Check the three recorded document time origins and both sample sets.
- Check both Chrome and ARsim cleanup in `session.json`. Each process tree runs in a private Windows job. The browser uses its own profile and an IPv4 loopback DevTools listener owned by that job. The session directory holds the profile, runtime copy, logs, and receipt. Retain the required evidence before removing the disposable session.
- The reported numeric values come from the rendered widget. Confirm the intended saved binding when selecting the fixture. The probe covers reopening the browser page during the same ARsim session. Use the [combined HMI workflow](arsim-hmi-smoke.md) to compare browser values with direct reads in one ARsim session. For a stale display, use the [direct OPC UA workflow](arsim-opcua-smoke.md) to isolate the PLC value. A network outage, ARsim restart, simulator shuttle movement, and controller or hardware acceptance each need their own checks.

The browser adapter uses Chrome DevTools for document navigation and DOM readback. [Chrome DevTools Page protocol](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-navigate) · [Chrome DevTools Runtime protocol](https://chromedevtools.github.io/devtools-protocol/tot/Runtime/#method-evaluate)
