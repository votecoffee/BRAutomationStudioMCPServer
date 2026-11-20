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

## Interpret the comparison

- The readback must identify the original `.lpj` by SHA-256. The route follows recipe IDs through a single unbranched cycle. Its terminal return marker shares the starting geometry ID, which provides the final directed return edge.
- Stations and gates use their geometry names and expected `Station` and `Waypoint` types. Geometry outside the saved cycle is reported separately. Each typed edge is counted, so a valid but different destination, reverse edge, duplicate, or missing return fails the comparison.
- With a retained [native build](native-build.md), the operation uses [RUC readback](ruc-review.md) to verify the saved inputs and compiled manager before comparing the LaunchPad route. Keep the selected phase, source and readback hashes, any rename file, and the build hash.
- Extra manager objects require review. A match covers the selected route topology. Compare geometry, coordinate transforms, recipe behavior, runtime station events and native `.x6d` import separately.
