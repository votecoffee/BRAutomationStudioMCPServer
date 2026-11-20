# Compare LaunchPad station and waypoint geometry

Use `launchpad_geometry_review` after [decoding the saved LaunchPad cycle](launchpad-route-review.md). It checks the same route and reads the absolute station and waypoint positions from the object lists referenced by the selected manager. With a retained `jobDirectory`, the positions come from the compiled BR configuration modules in the verified RUC.

## Run the measurement

Use `br-studio operation-help launchpad_geometry_review` to discover the request contract. Use the same project, manager, LaunchPad input, optional name map, and optional build directory as for the route review.

```json
{
  "schemaVersion": 1,
  "workspace": "geometry-review",
  "operation": "launchpad_geometry_review",
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

Run `br-studio run --request <geometry-review.json>`. Add `--format markdown` for the distance table. Confirm the input conventions against the selected version. This workflow interprets LaunchPad X/Y in millimetres and AS4 `Position=Absolute` X/Y in metres. The result includes the corresponding positions in millimetres and the source file or RUC member for each manager object.

## Interpret the geometry

- The pairwise distance table shows whether each object separation agrees after allowing for origin differences and rotation. A reflection preserves distances, so the review also compares the handedness of an identified three-object witness. A nearly collinear witness returns `indeterminate`.
- Without acceptance criteria, `geometryReview.state` is `measured`. Supply `distanceToleranceMicrometres` to check the largest absolute distance delta. Supply `expectedHandedness` of `preserved` or `reversed` when the coordinate conventions establish the expected orientation. Use the project's agreed criteria for those optional inputs.
- The report keeps the route and geometry outcomes. A topology match can coexist with a distance or orientation issue. Check the imported origin, positions relative to the segment grid, segment geometry, and trajectory clearance during native review.
