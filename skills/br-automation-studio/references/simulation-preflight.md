# Check the AS4 ACOPOS 6D complete simulation prerequisites

Use `simulation_preflight` with a saved project, an explicitly selected AS4 installation, and the intended mapp 6D family. The report combines [installer and library catalog checks](build-preflight.md) with the simulator and native export inputs for the motion rehearsal.

| Reviewed mapp 6D family | Minimum selector covered by this check | Matching B&R simulator release | Windows Installer product version | Reference segment firmware and application data |
| --- | --- | --- | --- | --- |
| 2.4 | 2.4.1 | 117.20.43 | 1.20.43 | 24-8A / 24-01 |
| 2.7 | 2.7 | 117.25.53 | 1.25.53 | 24-9F / 24-01 |

Choose the family from the expected project and hardware version set. The 2.4 row covers the earlier connection rehearsal with the reviewed 2.4.1 package. Use the 2.7 row for a project designed for 2.7 Traffic Manager behavior. [B&R AS4 system requirements](https://help.br-automation.com/#/en/4/motion/mapp_6d/allgemeines/systemvoraussetzung.html) · [2.4.1 release information](https://www.br-automation.com/en/downloads/software/automation-studio/tp-upgrades/v49-tp-upgrade-mapp6d-24110-revinfo/) · [B&R simulator catalog](https://www.br-automation.com/en-gb/downloads/mechatronic-systems/acopos-6d/planar-motor-simulation-tool/)

## Check the workstation before creating the project

Use `simulation_environment` with the intended AS4 installation and target family. It inventories the installed mapp 6D, mapp Motion and mapp View selectors. It verifies a manifest for the intended mapp 6D family, an installed mapp Motion candidate at the documented minimum or later, and a mapp View candidate. It also checks the AS build marker and exact Windows Installer registration. Pass the simulator ZIP to compare its bytes with the reviewed media.

```json
{
  "schemaVersion": 1,
  "workspace": "planar-simulation",
  "operation": "simulation_environment",
  "arguments": {
    "studioRoot": "C:/Program Files/BRAutomation4/AS412",
    "targetMapp6D": "2.7",
    "simulatorZip": "C:/Media/PlanarMotorSimulatorV117.25.53.zip"
  }
}
```

The workstation result covers catalog and installer metadata. Once the project exists, use `simulation_preflight` to check the exact saved package choices, selected CPU, Runtime, libraries and export.

## Verify the 2.7 companion ZIPs

With br-studio 0.48.0 or later, either simulation operation accepts these optional paths alongside `simulatorZip`.

| Argument | Reviewed ZIP |
| --- | --- |
| `controllerFirmwareZip` | `117-025-053.zip`, containing the ACOPOS 6D controller image. [B&R controller firmware](https://www.br-automation.com/en/downloads/mechatronic-systems/acopos-6d/acopos-6d-controller-firmware/) |
| `segmentFirmwareZip` | `FlywayUpdateTool_v1_2_7_FW249F_AD2401.zip`. Its reviewed installer lists several images, including `stator_update_FW249F_AD2401.pmiflwu` for the 2.7 row. [B&R Flyway downloads](https://www.br-automation.com/de-at/downloads/mechatronische-systeme/acopos-6d/acopos-6d-segment-firmware-update-tool-24-92-24-01/) |
| `maintenanceToolZip` | `PMTool117.26.01.zip`. [B&R PMTool downloads](https://www.br-automation.com/en-in/downloads/mechatronic-systems/acopos-6d/pmtool/) |

Read `companionMedia` under the selected operation's result. The `matched` state means the archive's SHA-256 agrees with the reviewed bytes for that role and family. A mismatched ZIP blocks a requested comparison. If a known ZIP is placed in the wrong role, the diagnostic identifies the observed release, including controller firmware supplied as the simulator. An optional companion archive requested for the 2.4 family is reported as unverified.

The archive comparison covers saved media. Windows Installer registration, the actual technology package, the native export, and the installed controller and segment versions have their own evidence.

## Run the preflight

Use `br-studio operation-help simulation_preflight` to discover the contract. Set `configuration` and `cpu` when the saved project offers more than one selection. Give the operation the native `.x6d` exported for the route when available. Include the B&R simulator ZIP to compare it with the reviewed release bytes. Omitting the export lets the report identify the missing artifact alongside any catalog or installation gaps.

```json
{
  "schemaVersion": 1,
  "workspace": "planar-simulation",
  "operation": "simulation_preflight",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "studioRoot": "C:/Program Files/BRAutomation4/AS412",
    "configuration": "ARsim",
    "cpu": "PC",
    "targetMapp6D": "2.7",
    "simulatorZip": "C:/Media/PlanarMotorSimulatorV117.25.53.zip",
    "launchPadExport": "C:/Projects/Example/Route.x6d"
  }
}
```

Run `br-studio run --request <simulation-preflight.json>`. Add `--format markdown` for the readable report.

## Inspect the LaunchPad export structure

With br-studio 0.49.0 or later, set `inspectLaunchPadExport` to true alongside `launchPadExport`. Read `simulationPreflight.launchPadExport.structure` for the XML root, group counts and available IDs and names. The check uses the same bytes identified by the export hash. It recognizes the unqualified top-level `Segments` group and inventories `Stations`, `Waypoints`, `Gates`, `Branches` and `ExclusiveGroups` when present.

The reference is the installed AS4 mapp 6D 2.2 importer. An export outside the observed XML structure produces an `unverified` result with a diagnostic and retains the file identity. Use the selected Automation Studio importer for format acceptance and the [LaunchPad route review](launchpad-route-review.md) for the saved and compiled Traffic Manager mappings. [B&R LaunchPad export workflow](https://www.br-automation.com/en/products/software/acopos-6d-launchpad/)

## Verify the MpTraffic definition before building

Set `arguments.verifyMpTrafficEnum` to true with br-studio 0.34.0 or later when the project contains its registered MpTraffic library. The comparison reads the project .apj, the selected CPU's library references and the saved library descriptor. It compares the detection enum names and integer values with the exact mapp 6D and MpTraffic versions selected from the installed catalog. It also compares the saved and installed IA32 library binaries by SHA-256.

Use `simulationPreflight.mpTrafficDefinition` for the result, both event maps, paths and hashes. A mismatch or unavailable definition blocks the requested verification. Formatting changes in `MpTraffic.typ` can have different hashes with the same event map. The comparison describes the saved selector even when the requested simulation family still has other prerequisites missing. Set the same option in [native build planning and execution](native-build.md#verify-mptraffic-in-the-plan-and-ruc) to retain the verified files through compilation. Review and decode the captured events with [project-bound event decoding](cycle-scan-capture.md#decode-events-from-the-saved-project-and-ruc).

## Read the result

- Check the project's saved selector against the intended family and the installed `TP.cfg`. The report shows each target-family selector and internal build. It checks the exact saved selector, the documented AS, Runtime and mapp Motion minimums, the mapp View selection, and the selected `McAcp6D` and `MpTraffic` library descriptors.
- Review the simulator ZIP and Windows Installer results separately. The ZIP SHA-256 identifies an exact previously reviewed release. The installer query checks the known product code and version registered for the current Windows user. The Planar Motor maintenance tool serves a different role. [Windows Installer product state](https://learn.microsoft.com/en-us/windows/win32/api/msi/nf-msi-msiqueryproductstatew) · [Windows Installer product metadata](https://learn.microsoft.com/en-us/windows/win32/api/msi/nf-msi-msigetproductinfow)
- The native export result records the selected file's size and SHA-256. Geometry, station mappings, recipes and compatibility need the native import and subsequent readback. The report's `inputs_present` state covers the reviewed prerequisites. A run can also report `blocked` or `review_required`. Keep any warnings with the receipt.
- Run the matched simulation separately to establish the TCP connection, observe a shuttle through the route, and check the station handshake. [B&R complete simulation procedure](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/vollstandige_simulation.html)
