# Read station paths and LaunchPad model waits

Use `launchpad_station_review` to turn a matched LaunchPad cycle into paths between consecutive stations. Run it with the same inputs as [LaunchPad route review](launchpad-route-review.md). A leg includes its source Station, any intervening Waypoints, and the next Station. The final leg follows the return marker through to its station destination.

Use the [inspection trace review](station-trace-review.md) with a retained compiled job when checking recorded inspection results, Release and destination arrival for one of those legs.

## Run the review

Discover the request with `br-studio operation-help launchpad_station_review`. Start with the route review request and set `operation` to `launchpad_station_review`. Retain the original `.lpj` and its decoded JSON readback. Use `nameMap` if names changed on import. Add `jobDirectory` when reviewing a source-matched RUC, then run `br-studio run --request station-review.json --format markdown`.

## Read the result

- `routeReview` compares the typed directed edges with the selected manager. `stationReview.legs` is populated when the route matches. Each leg keeps the saved geometry names and imported names, and identifies whether it crosses the terminal return marker.
- Each leg's `modelProcess` describes its **source station**. The report retains the ordered Wait entries, raw `dwell_time` values, `fixed_dwell_time` flag, and recipe-node ID. The return marker provides the last edge. It does not add a process step at the starting station.
- The supported process shape is the Wait array observed in LaunchPad 1.4. An explicitly empty array is readable. Missing fields, malformed durations or unfamiliar steps produce an unverified process while preserving the matched paths. No conversion or total is applied to the raw durations.

Use [LaunchPad recovery review](launchpad-recovery-review.md) to check the station declarations before the first recovery test. Follow the [motion parameter review](launchpad-motion-review.md) to inspect the assembly caps and modeled exit values before reviewing PLC release settings.

## Verify the native recipe

`stationReview.nativeRecipe` records `not_checked`. In target Automation Studio, record the linked recipe and product label, each station's Process, and the Transition and selected destination. The complete path from the report gives the expected intermediate waypoints for the station leg. A waypoint reports a mapping along the path. Its name does not by itself identify the destination station of the recipe transition. Then verify recipe activation, the release command, and a fresh arrival with the same shuttle identity in the matched simulator. [B&R mapp 6D recipe management](https://help.br-automation.com/#/en/4/motion/mapp_6d/konzept/traffic_manager/recipe_management.html)
