# Check the AS4 ACOPOS 6D complete simulation prerequisites

Use `simulation_preflight` with a saved project, an explicitly selected AS4 installation, and the intended mapp 6D family. The report combines [installer and library catalog checks](build-preflight.md) with the simulator and native export inputs for the motion rehearsal.

| Reviewed mapp 6D family | Minimum selector covered by this check | Matching B&R simulator release | Windows Installer product version | Reference segment firmware and application data |
| --- | --- | --- | --- | --- |
| 2.4 | 2.4.1 | 117.20.43 | 1.20.43 | 24-8A / 24-01 |
| 2.7 | 2.7 | 117.25.53 | 1.25.53 | 24-9F / 24-01 |

Choose the family from the expected project and hardware version set. The 2.4 row covers the earlier connection rehearsal with the reviewed 2.4.1 package. Use the 2.7 row for a project designed for 2.7 Traffic Manager behavior. [B&R AS4 system requirements](https://help.br-automation.com/#/en/4/motion/mapp_6d/allgemeines/systemvoraussetzung.html) · [2.4.1 release information](https://www.br-automation.com/en/downloads/software/automation-studio/tp-upgrades/v49-tp-upgrade-mapp6d-24110-revinfo/) · [B&R simulator catalog](https://www.br-automation.com/en-gb/downloads/mechatronic-systems/acopos-6d/planar-motor-simulation-tool/)

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

## Read the result

- Check the project's saved selector against the intended family and the installed `TP.cfg`. The report shows each target-family selector and internal build. It checks the exact saved selector, the documented AS, Runtime and mapp Motion minimums, the mapp View selection, and the selected `McAcp6D` and `MpTraffic` library descriptors.
- Review the simulator ZIP and Windows Installer results separately. The ZIP SHA-256 identifies an exact previously reviewed release. The installer query checks the known product code and version registered for the current Windows user. The Planar Motor maintenance tool serves a different role. [Windows Installer product state](https://learn.microsoft.com/en-us/windows/win32/api/msi/nf-msi-msiqueryproductstatew) · [Windows Installer product metadata](https://learn.microsoft.com/en-us/windows/win32/api/msi/nf-msi-msigetproductinfow)
- The native export result records the selected file's size and SHA-256. Geometry, station mappings, recipes and compatibility need the native import and subsequent readback. The report's `inputs_present` state covers the reviewed prerequisites. A run can also report `blocked` or `review_required`. Keep any warnings with the receipt.
- Run the matched simulation separately to establish the TCP connection, observe a shuttle through the route, and check the station handshake. [B&R complete simulation procedure](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/vollstandige_simulation.html)
