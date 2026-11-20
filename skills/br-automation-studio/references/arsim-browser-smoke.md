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

## Verify the default start page in ARsim

Set `requireStartPage` to `true` in br-studio 0.40.0 or later to check that the selected Assembly6D widget is assigned to the default visualization's start page. The operation uses the [saved and compiled placement review](ruc-review.md#verify-the-default-page-placement-of-assembly6d) on the selected build before starting ARsim. Supply `assemblyWidgetId` when several Assembly6D widgets exist.

The owned ARsim listener must return the exact packaged visualization client map at the lowercase URL used by the AS4 browser. For example, the RUC member `ExampleVisu.json` corresponds to `/examplevisu.json`. The HTTP record identifies the archive member and the response SHA-256. Both browser documents must record a same-origin XHR or fetch for that map with HTTP 200 or 304. See `startPageMap` under `initial` and `reopened` in the browser receipt.

## Verify the Assembly6D bundle in both documents

Set `requireWidgetBundle` to `true` in br-studio 0.38.0 or later to require the AS4 release bundle. The CLI finds `BRVisu/release/brease.js` in the reviewed RUC, checks for one named `widgets/mapp6D/Assembly6D/Assembly6D` AMD declaration, and compares the bytes served by the owned HTTP listener with that RUC member. The HTTP receipt records the archive path, module name and SHA-256.

Each browser document must also show a same-origin script load for `/BRVisu/release/brease.js` with HTTP 200 or 304. See `widgetBundle` in both the `initial` and `reopened` document records. The browser status can reflect revalidation of its cache. The separate HTTP check supplies the byte comparison. The evidence covers the AS4 release bundle and document loads. Use the layout and counter checks for the rendered widget and PLC updates. [W3C Resource Timing](https://www.w3.org/TR/resource-timing/)

## Check the layout assets

Use the [file-device review](layout-device-review.md) to verify the selected CPU's saved and compiled `6DData` declaration.

The browser report includes the expected `6DData` SVG requests and an inline SVG count for each observed HMI document. Set `requireLayoutAssets` to `true` to require HTTP 200 or 304 for the assembly SVG and the linked manager SVG (when configured), plus an inline SVG with nonzero layout bounds inside the widget. The counter and SVG checks use the same document window. The SVG result covers the completed requests and DOM bounds. Visual accuracy and live shuttle motion require target evidence.

The expected URLs are derived from the compiled widget links as `/FileDevice:6DData/<symbol>.svg`. The report uses browser resource timing for those exact URLs. An unavailable status or missing inline bounds leaves the layout unverified. An HTTP error is reported as `request_failed`. [B&R layout display troubleshooting](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/faq.html)

### Save the generated SVGs

Set `captureLayoutAssets` to `true` in the request arguments. This also enables the required layout check in both browser documents. After the browser closes, the CLI makes a fresh GET for each expected URL on the same owned ARsim HTTP listener. Each response must be HTTP 200 with an `image/svg+xml` body and a namespaced SVG root. The browser status and capture refer to separate requests inside the same ARsim session.

Keep `session.json` and the `layout-assets/` directory together. The `layoutCapture` section records the listener and each URL, local path, byte count, media type and SHA-256. Its `assembly` and `trafficManager` records identify the files to use for `assemblySvg` and `trafficSvg` in the [generated layout review](layout-svg-review.md). A widget with only an assembly link yields a single assembly SVG.

## Interpret the receipts

- Check for `arsim_loopback_browser` in `result.evidence` and `complete` in `result.arsimSmoke.browser.state`. The receipt includes the pinned RUC and HTTP asset checks, Chrome version, owned debugger endpoint, widget IDs, and the numeric samples.
- The initial document needs the selected Assembly6D widget and three increasing positive values. The probe confirms an intervening blank document, then requires a separate HMI document with the same widget type and two increasing values above the last sample from the first document. Zero placeholders and repeated values are excluded. Check the three recorded document time origins and both sample sets.
- Check both Chrome and ARsim cleanup in `session.json`. Each process tree runs in a private Windows job. The browser uses its own profile and an IPv4 loopback DevTools listener owned by that job. The session directory holds the profile, runtime copy, logs, and receipt. Retain the required evidence before removing the disposable session.
- The reported numeric values come from the rendered widget. Confirm the intended saved binding when selecting the fixture. The probe covers reopening the browser page during the same ARsim session. Use the [combined HMI workflow](arsim-hmi-smoke.md) to compare browser values with direct reads in one ARsim session. For a stale display, use the [direct OPC UA workflow](arsim-opcua-smoke.md) to isolate the PLC value. A network outage, ARsim restart, simulator shuttle movement, and controller or hardware acceptance each need their own checks.

The browser adapter uses Chrome DevTools for document navigation and DOM readback. [Chrome DevTools Page protocol](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-navigate) · [Chrome DevTools Runtime protocol](https://chromedevtools.github.io/devtools-protocol/tot/Runtime/#method-evaluate)
