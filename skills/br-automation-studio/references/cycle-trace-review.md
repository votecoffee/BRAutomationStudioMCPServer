# Review a two-station inspection cycle trace

Use `cycle_trace_review` for a saved scan capture of one shuttle through an inspection leg, its return and the next held request. The source-station argument selects the inspection leg. The saved LaunchPad cycle and compiled Traffic Manager must define two station legs that return to the starting station. Use the same verified project inputs as the [inspection station trace review](station-trace-review.md).

## Run the review

Use `br-studio operation-help cycle_trace_review` for the request contract. Set the operation and replace the trace path in a copy of the [inspection leg request](station-trace-review.md#bind-the-trace-to-the-route). Run `br-studio run --request <request.json>` or add `--format markdown` for a readable report.

The cycle JSON has `schemaVersion` set to 1 and three objects.

| Object | Contents |
| --- | --- |
| `inspectionLeg` | A complete [station trace](station-trace-review.md) with the source .lpj and RUC hashes and the captured request and shuttle. |
| `returnLeg` | Return source and destination identifiers, starting monitor sequence and consecutive scan observations. |
| `nextHold` | The first captured hold for a distinct request at the original station after return, with the same shuttle. |

## Record the return leg

Use the same PLC scan index as the outbound trace. Start while the inspected shuttle occupies the return source station. This may overlap the outbound capture from its accepted arrival onward. A return starting after the outbound interval must begin at the next scan.

In `returnLeg`, set `sourceStation` and `destinationStation` to their imported identifiers. Set `monitorSequenceAtStart` to the last consumed destination event sequence before watching the return. Include each subsequent scan in `scans`.

- Record `scan`, `sourceHealthy` and `monitorHealthy`. Base validity on the active source observation and destination monitor, including queue continuity. Set `occupyingShuttleId` to the positive ID of the single shuttle at the return source. Use `null` after observing valid empty occupancy.
- Use the same `detections` list as the station trace. Preserve the station, shuttle, sequence and event enum. The cycle check requires a fresh `mpTRAFFIC_SH_ARRIVAL` at the original inspection station. It accepts either order of observing the return source clear and the destination arrival.
- Include Boolean `error` and integer `statusId` on a return error. The reviewer preserves that status and stops the return check.

Source occupancy and destination arrival support this check whether the recipe drives the return or station control requests it. Use the normalized source-clear observation for the return. The reported waypoint names come from the compiled route. Verify actual waypoint traversal separately when needed.

## Record the next request

Capture `nextHold` after the outbound interval and both return observations. Use a scan covered by the return capture or its immediately following scan. Record `scan`, `station`, `shuttleId`, `requestId`, `held`, `release` and `resultAccepted`. The new request needs a different ID, the same returned shuttle and `held: true` with `release` and `resultAccepted` clear.

Keep the raw PLC export, normalized trace, review output and RUC job together. The review records the trace SHA-256, compiled paths and decisive scan indexes. A gap or missing witness yields `incomplete`. An identity conflict, premature hold or recorded adapter error yields `issues`. For a native HMI acceptance run, verify the browser shows the same next request after reopening and keep that separately timestamped evidence.
