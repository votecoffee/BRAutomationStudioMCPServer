# Compare route positions with the segment grid

Use `launchpad_frame_review` after [decoding the LaunchPad save](launchpad-route-review.md). The operation traces the selected Traffic Manager to its linked 240 mm S3 layout and reads the station and waypoint positions. It measures the error under each signed X/Y mapping that aligns the segment grids relative to their masters.

## Run the review

Use `br-studio operation-help launchpad_frame_review` for the request fields. Run `br-studio run --request frame.json` or add `--format markdown` for a readable summary. Include `jobDirectory` to read the selected configuration from a retained RUC whose source has been verified. Omit it to read the saved configuration.

```json
{
  "schemaVersion": 1,
  "workspace": "frame-review",
  "operation": "launchpad_frame_review",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "configuration": "ARsim",
    "cpu": "PC",
    "managerId": "DemoTraffic",
    "launchPadProject": "C:/Projects/Example/Route.lpj",
    "launchPadReadback": "C:/Toolset/.local/route-readback.json",
    "jobDirectory": "C:/BuildJobs/example-arsim-001"
  }
}
```

## Read the residuals

- For each compatible axis mapping the report fits a single translation by least squares from the paired route objects. The smallest RMS residual identifies the best of those fits. Compare the maximum residual and per-object X/Y residuals as well.
- Each object implies a translation that would align that object exactly. For any pair, half the distance between their implied translations is a lower bound on the worse of their residuals under any one translation. The report names the limiting pair for each axis mapping and reports the smallest of these bounds across all compatible mappings.
- The default run reports measurements alone. If you supply `positionToleranceMicrometres`, at least one reported transform must keep every object within that tolerance. If the lower bound exceeds the tolerance, every translation under every grid-compatible mapping fails. If only the reported fits exceed the tolerance, the result applies to those fits.
- A matching master-relative grid supports the listed axis mappings. A fitted shift describes the route data. Verify the actual import origin and physical clearances in the target Automation Studio version.

## Fix the translation to a verified common point

Supply `datumFile` to use the same known point measured in both coordinate frames. Copy the three identity fields from `frameReview.inputIdentity` for the exact saved or packaged inputs reviewed. The Automation Studio hash identifies the saved read set or the compiled RUC archive, respectively. Recheck the datum after changing those inputs. Obtain the paired coordinates from the native workflow or a reviewed survey. The layout grid indexes and raw `UserOrigin` fields do not provide that point.

```json
{
  "schemaVersion": 1,
  "launchPadSha256": "<copy from frameReview.inputIdentity>",
  "automationStudioSha256": "<copy from frameReview.inputIdentity>",
  "layoutId": "FixtureLayout",
  "label": "Verified reference point",
  "launchPadMm": [20, 10],
  "automationStudioMm": [30, 50]
}
```

The translation for each candidate moves the mapped reference point to its Automation Studio coordinates. The JSON report records the datum file hash, per-object residuals, and lower-bound witness.

## Trace the coordinates into the HMI

1. Run `launchpad_frame_review` on the saved project and again with the reviewed `jobDirectory`. When supplying a datum, use each report's own input identity. A constructed rehearsal can use its declared placement as the datum. Record it as a rehearsal placement and use a point measured in both frames for target acceptance.
2. Use the same project and job in `arsim_hmi_smoke` with `captureLayoutAssets` enabled. Retain the capture receipt, both SVGs and the RUC hash. [Combined HMI rehearsal](arsim-hmi-smoke.md)
3. Pass those SVGs to `layout_svg_review` with the same job and manager. Compare the RUC SHA-256 across the compiled frame, HMI and SVG reports. Compare the SVG SHA-256 values with the capture receipt. Together the reports trace the saved model through the compiled configuration to the positions and link IDs served to the HMI. [Generated layout review](layout-svg-review.md)
