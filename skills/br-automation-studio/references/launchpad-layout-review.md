# Compare a LaunchPad segment layout with the linked AS4 layout

Use `launchpad_layout_review` after [decoding the original LaunchPad save](launchpad-route-review.md). It compares the same saved route, follows the selected Traffic Manager through its assembly to one layout, and reads the segment positions and declared master. Supplying `jobDirectory` selects the layout in a verified retained RUC.

## Run the segment comparison

Use `br-studio operation-help launchpad_layout_review` to discover the request contract. Use the same project, configuration, CPU, manager ID, LaunchPad source, and JSON readback as the route review. Supply the retained native build directory when checking compiled configuration.

```json
{
  "schemaVersion": 1,
  "workspace": "layout-review",
  "operation": "launchpad_layout_review",
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

Run `br-studio run --request <layout-review.json>`. Add `--format markdown` for the readable report. This readback supports the square S3-AS 240 mm grid. The AS4 Layout configuration Help identifies `GridSize=0`, `TypeGrid240=0`, grid-index X/Y fields, and the master flag for the reviewed serialization. [B&R S3 segment range](https://www.br-automation.com/en/products/mechatronic-systems/acopos-6d/segments-s3/)

## Interpret the layout result

- Each side must have exactly one master and one segment per occupied grid cell. The operation reads the entire linked layout and compares the count and each X/Y offset relative to that master. A spacing change, displaced master, or missing segment can fail the layout check while the route still matches.
- The report records all signed axis mappings that align the layouts after translation. For example, `-y, x` maps the LaunchPad X/Y components to the Automation Studio frame after a quarter-turn. A straight row can leave more than one mapping compatible. Use the [station and waypoint geometry review](launchpad-geometry-review.md) and the native coordinate convention to establish orientation.
- Master coordinates and the Automation Studio `UserOrigin` values are retained for review. Confirm the actual machine origin, segment part numbers, and relative station positions during the native import and clearance review.
