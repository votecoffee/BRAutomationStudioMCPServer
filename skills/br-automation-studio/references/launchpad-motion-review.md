# Read in-plane assembly limits and LaunchPad exit values

Use `launchpad_motion_review` with the [LaunchPad route review](launchpad-route-review.md) inputs. The operation follows the selected Traffic Manager to its assembly and reads `Shuttles/Limits/InPlaneLimits`. Include `jobDirectory` to compare the saved assembly with a source-matched RUC. Run `br-studio run --request motion.json --format markdown` for the summary.

## Read the configured limits

- `InPlaneVelLim` is expressed in m/s and `InPlaneAccLim` in m/s². The observed AS4 form comes from the mapp 6D 2.2 editor catalog, `Editors/Assembly6D/Assembly6DCfg.xml` and `ShuttleLimits6D.xml`. [B&R assembly configuration Help](https://help.br-automation.com/#/en/4/motion/mapp_6d/konfiguration/komponenten/assembly.html)
- A saved value may be absent. The comparison reports `materialized` if the verified RUC contains the limit and the saved file leaves it absent. This records what the build supplied. An explicit saved limit must be present with the same numeric value in the RUC. Different decimal spellings of the same value compare equal.
- Unsupported or ambiguous declarations make the result unverified. In a compiled review, a missing or different value for an explicitly declared saved limit is an issue.

Use `verifyLaunchPadMotionLimits` to require explicit positive values in the saved assembly and carry them into the build plan with the model's raw exit values. [Pin the explicit in-plane motion limits](native-build.md#pin-the-explicit-in-plane-motion-limits)

## Read the model and verify the command

The report preserves the raw LaunchPad exit velocity, acceleration, and payload weight for each source on the matched route. The terminal return marker contributes the final edge, with no separate exit record. Negative values remain raw. Verify their interpretation in the compatible LaunchPad and native import workflow before applying engineering units or defaults.

For the first station release in the target system, record the configured assembly caps and the actual `MpTrafficStationControl.ExitVelocity` and `ExitAcceleration` inputs, along with the expected destination. These inputs are the requested exit setpoints documented by B&R. Verify the actual motion with the version-matched simulator under the approved limits and payload. [B&R station-control pins](https://help.br-automation.com/#/en/4/motion/mapp_6d/bibliotheken/mptraffic/function_block/mptrafficstationcontrol/fb_mptrafficstationcontrol.html)
