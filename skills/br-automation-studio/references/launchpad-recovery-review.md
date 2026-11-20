# Review station recovery declarations

Use `launchpad_recovery_review` after [LaunchPad route review](launchpad-route-review.md). The operation pairs the same saved geometry names and imported Station names in the selected Traffic Manager. Use the same inputs as the route review and set `operation` to `launchpad_recovery_review`. Include `jobDirectory` to read a source-matched RUC. Run `br-studio run --request recovery.json --format markdown` for the summary.

## Compare the declarations

- The report keeps the LaunchPad model's starting-position flags separate from each station's own recovery declaration. Compare the declared mode, whether the station itself is a recovery point, and the count of explicit points.
- The AS4 reader uses the observed `RecoveryPoints` group, `Mode`, `StationIsRecoveryPoint`, and `RecoveryPoint[n]` entries with Absolute X/Y in metres. Missing declarations and unsupported point forms produce unverified evidence. An explicit empty list counts as zero. The format comes from the AS4 mapp 6D 2.2.0 editor catalog's `Station.xml` and `Position.xml`.
- In a compiled review, compare the saved and packaged AS4 declarations and indexed point coordinates. Different decimal spellings of the same coordinate are equivalent. Moving coordinates between indices changes the configured sequence and fails the comparison.
- LaunchPad contributes its explicit point count. Compare point coordinates across formats only after verifying the common frame. The tool reports no capacity or shuttle assignment from these flags. Verify the effective eligibility, recovery path and occupancy in the target simulator. [B&R station recovery points](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/recovery_punkte_in_den_stationen_definieren.html)
