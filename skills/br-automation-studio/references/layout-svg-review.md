# Review generated Assembly6D layout SVGs

Use `layout_svg_review` after collecting both generated SVGs from an AS4 mapp View HMI rehearsal. Enable `captureLayoutAssets` to collect them with `arsim_hmi_smoke` or `arsim_browser_smoke`. Keep the associated RUC and native build receipts. The operation verifies the saved source against the build inputs and the RUC against its recorded hash. It reads the two SVGs as XML and records their SHA-256. [Capture workflow](arsim-browser-smoke.md#save-the-generated-svgs)

## Run the comparison

Save a request in the project folder or the checkout's ignored `.local/` directory. Paths resolve from the request directory.

```json
{
  "schemaVersion": 1,
  "workspace": "layout-review",
  "operation": "layout_svg_review",
  "arguments": {
    "project": "project/Demo.apj",
    "jobDirectory": "retained-build",
    "managerId": "DemoTraffic",
    "assemblySvg": "captured/FixtureAssembly.svg",
    "trafficSvg": "captured/DemoTraffic.svg"
  }
}
```

```powershell
$brStudio = '.\.local\venv\Scripts\br-studio.exe'
& $brStudio run --request .local/layout-svg.json --format markdown
```

## Interpret the evidence

- The manager resolves to a saved and compiled assembly and layout. The operation checks the segment cells and unique master in the 240 mm S3 layout, including the corresponding SVG rectangle sizes and translations.
- The saved and compiled absolute XY positions must agree. The review compares them with the station rectangle centers and waypoint ellipse centers in the SVG. This format uses millimetres and inverted Y. The largest per-axis difference permitted is 0.001 mm.
- Each compiled directed mapping needs the expected SVG link ID and one-way class. Extra, missing or reversed link IDs fail review. Verify the drawn line endpoints during target HMI review.
- The verified format is from AS4 and mapp 6D 2.2. It uses an explicit zero user origin, absolute station and waypoint positions, and distinct one-way mappings. An unsupported coordinate frame or mapping topology returns `partial`.

Match the reported SVG hashes to the HTTP capture receipt when proving what the HMI served. Confirm page loading and widget bounds with the [combined HMI rehearsal](arsim-hmi-smoke.md). Target appearance and shuttle movement require observation in the intended environment.

For a cycle and held-status review, supply this operation's complete JSON output as `hmiLayoutSvgReport`. The cycle reviewer compares both hashes with the layout capture in the same HMI status report. [Pair the layout geometry with the cycle](cycle-trace-review.md#match-the-captured-layout-geometry-to-the-cycle)

Use the [coordinate frame workflow](launchpad-frame-review.md#trace-the-coordinates-into-the-hmi) when the review also needs to trace the positions to a saved LaunchPad model.
