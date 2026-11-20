# Review generated Assembly6D layout SVGs

Use `layout_svg_review` after collecting both generated SVGs from an AS4 mapp View HMI rehearsal. Keep the associated RUC and native build receipts. The operation verifies the saved source against the build inputs and the RUC against its recorded hash. It reads the two SVGs as XML and records their SHA-256.

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
