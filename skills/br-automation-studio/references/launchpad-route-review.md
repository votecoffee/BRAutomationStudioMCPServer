# Compare a saved LaunchPad route with a Traffic Manager

Use `launchpad_route_review` to compare one saved LaunchPad cycle with the typed objects and directed mappings of an AS4 Traffic Manager. Select the exact manager element ID in the saved project. Supply a retained native build to check the compiled manager referenced by the same project. [B&R LaunchPad project export](https://www.br-automation.com/en/products/software/acopos-6d-launchpad/)

## Decode the original save

From the checkout, use Node.js and a local installation of Zipson 0.2.12. Put the readback alongside the project evidence or under the ignored `.local/` directory. The decoder reads the supported Zipson `.lpj` object, records the original bytes' SHA-256, and creates a new UTF-8 JSON readback. [Zipson parser](https://github.com/jgranstrom/zipson)

```powershell
npm install --prefix .local/launchpad-codec --no-save --package-lock=false --ignore-scripts zipson@0.2.12
node scripts/decode-launchpad.cjs 'C:/Projects/Example/Route.lpj' '.local/route-readback.json' '.local/launchpad-codec/node_modules/zipson'
```

Use `br-studio operation-help launchpad_route_review` to discover the request contract. `configuration` and `cpu` select the saved inspection target. With `jobDirectory` the retained build selects them, and any supplied selection must agree.

```json
{
  "schemaVersion": 1,
  "workspace": "route-review",
  "operation": "launchpad_route_review",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "configuration": "ARsim",
    "cpu": "PC",
    "managerId": "DemoTraffic",
    "launchPadProject": "C:/Projects/Example/Route.lpj",
    "launchPadReadback": "C:/Toolset/.local/route-readback.json"
  }
}
```

Run `br-studio run --request <route-review.json>`. Add `--format markdown` for the readable report. If the import renamed objects, pass `nameMap` as a path to a UTF-8 JSON object, for example `{"Transfer":"Transit"}`. Omitted names keep their saved identifiers. Each expected route object must map to a distinct manager name.

## Compare the export before import

With br-studio 0.50.0 or later, use `launchpad_export_review` on the original save, its decoded readback and the exported file. This check can run before creating an Automation Studio project.

```json
{
  "schemaVersion": 1,
  "workspace": "route-review",
  "operation": "launchpad_export_review",
  "arguments": {
    "launchPadProject": "C:/Projects/Example/Route.lpj",
    "launchPadReadback": "C:/Toolset/.local/route-readback.json",
    "launchPadExport": "C:/Projects/Example/Route.x6d"
  }
}
```

The operation compares the segment count, exact object names and types, and directed edge counts for a single saved cycle. In the observed AS4 2.2 XML structure, it resolves the `Value` entries under `NextStations`, `NextWaypoints`, `NextGates` and `NextBranches` by object ID. Reported differences include unresolved IDs, wrong endpoint types and return edges aimed at a different station. Keep the save, readback and export hashes from the result.

The reference is the installed AS4 2.2 importer. A structure outside that readback or ambiguous records leave the route unverified. Use the selected native importer and the saved or compiled Traffic Manager comparison for import acceptance. Use the geometry and recipe workflows for positions, process steps and runtime behavior.

## Interpret the comparison

- The readback must identify the original `.lpj` by SHA-256. The route follows recipe IDs through a single unbranched cycle. Its terminal return marker shares the starting geometry ID, which provides the final directed return edge.
- Stations and gates use their geometry names and expected `Station` and `Waypoint` types. Geometry outside the saved cycle is reported separately. Each typed edge is counted, so a valid but different destination, reverse edge, duplicate, or missing return fails the comparison.
- With a retained [native build](native-build.md), the operation uses [RUC readback](ruc-review.md) to verify the saved inputs and compiled manager before comparing the LaunchPad route. Keep the selected phase, source and readback hashes, any rename file, and the build hash.
- Extra manager objects require review. A match covers the selected route topology. Use [LaunchPad geometry review](launchpad-geometry-review.md) for spacing and handedness. Use [LaunchPad station review](launchpad-station-review.md) for station-to-station paths and model waits before checking the native recipe and runtime station events.
