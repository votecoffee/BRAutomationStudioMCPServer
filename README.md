# BRAutomationStudioMCPServer

Standalone engineering tools for B&R Automation Studio projects.

The first workflow inspects saved Automation Studio projects for ACOPOS 6D, Core Motion, Traffic Manager, and PLC-hosted mapp View. The standalone CLI uses a shared operation catalog that a future MCP adapter can call.

## Table of Contents

- [Install](#install)
- [Usage](#usage)
- [Chat workflow](#chat-workflow)
- [Architecture](#architecture)
- [Maintainers](#maintainers)
- [Contributing](#contributing)
- [License](#license)

## Install

Use Python 3.11 or later. From PowerShell, clone the repository and install the CLI in a local environment.

```powershell
git clone https://github.com/votecoffee/BRAutomationStudioMCPServer.git
Set-Location BRAutomationStudioMCPServer
py -3 -m venv .local\venv
.\.local\venv\Scripts\python.exe -m pip install .
```

Saved-file inspection runs without Automation Studio. Native AS4 builds require a compatible B&R installation on Windows.

## Usage

### CLI

```powershell
$brStudio = '.\.local\venv\Scripts\br-studio.exe'
& $brStudio operations
& $brStudio operation-help project_inspect
& $brStudio operation-help build_preflight
& $brStudio operation-help simulation_environment
& $brStudio operation-help simulation_preflight
& $brStudio operation-help build_plan
& $brStudio operation-help build_execute
& $brStudio operation-help ruc_review
& $brStudio operation-help assembly_io_review
& $brStudio operation-help layout_device_review
& $brStudio operation-help layout_svg_review
& $brStudio operation-help launchpad_route_review
& $brStudio operation-help launchpad_export_review
& $brStudio operation-help launchpad_station_review
& $brStudio operation-help station_trace_review
& $brStudio operation-help cycle_trace_review
& $brStudio operation-help launchpad_recovery_review
& $brStudio operation-help launchpad_motion_review
& $brStudio operation-help launchpad_geometry_review
& $brStudio operation-help launchpad_layout_review
& $brStudio operation-help launchpad_frame_review
& $brStudio operation-help ruc_binding_review
& $brStudio operation-help arsim_smoke
& $brStudio operation-help arsim_browser_smoke
& $brStudio operation-help arsim_opcua_smoke
& $brStudio operation-help arsim_hmi_smoke
& $brStudio operation-help arsim_status_smoke
& $brStudio run --request examples/inspect-loop.json
& $brStudio run --request examples/inspect-loop.json --format markdown
```

The example reads a synthetic project. The result lists declared package versions, selected configuration and CPU, layout and assembly references, Traffic Manager endpoints, and Assembly6D widget links. It records SHA-256 hashes for the files read. Use the result as saved-file evidence. Native compilation and ARsim behavior need their own checks.

For an installed AS4 toolchain, `build_preflight` compares the project with the selected Studio, Runtime, CPU and technology-package catalogs. [Build preflight workflow](skills/br-automation-studio/references/build-preflight.md)

Use `widget_package_review` to inventory saved mapp View widget library entries before a build or join them to a retained build log and RUC. [Widget package review](skills/br-automation-studio/references/widget-package-review.md)

Use `simulation_environment` to check the AS4 package catalog, simulator registration and saved media during workstation setup. It can identify the reviewed 2.7 controller, Flyway and maintenance archives by hash. Use `simulation_preflight` with `inspectLaunchPadExport` to fingerprint the native export and inventory the XML groups observed in the AS4 2.2 importer. [Simulation prerequisites and MpTraffic definition check](skills/br-automation-studio/references/simulation-preflight.md)

Use `build_plan` and `build_execute` to compile a pinned AS4 PC simulation project and collect the RUC archive and builder log. Build dispatch supports short Windows drive aliases for deeply nested jobs. Use `verifyMotionPayloads` to verify the six selected SG4/IA32 motion binaries in the RUC. Use `verifyMotionDeclarations` to check the selected McAcp6D and MpTraffic IEC declaration files during preflight and planning. Use `verifyMotionSymbols` to check the IEC globals the build generates for the selected assemblies and Traffic Managers. [Native build workflow](skills/br-automation-studio/references/native-build.md)

After building, use `ruc_review` to compare the retained package's 6D modules, typed routes, linked exclusivity groups, and rendered widget links with the saved inputs. Supply `startPageWidgetId` to verify the default page placement of an Assembly6D widget. [RUC review workflow](skills/br-automation-studio/references/ruc-review.md) · [Traffic rule review](skills/br-automation-studio/references/traffic-rule-review.md)

Use `motion_interface_review` to look up the public pins, nested IEC types and explicit enum values from the verified McAcp6D and MpTraffic declarations. [Function-block interface workflow](skills/br-automation-studio/references/build-preflight.md#read-selected-function-block-pins)

Use `assembly_io_review` to trace the selected assembly's communication variable references to saved IEC declarations and check its compiled read-axis flags. [Assembly I/O workflow](skills/br-automation-studio/references/assembly-io-review.md)

Use `layout_device_review` to verify the selected CPU's saved and compiled `6DData` declaration before checking the runtime SVGs. [Layout file-device workflow](skills/br-automation-studio/references/layout-device-review.md)

Use `layout_svg_review` to compare the generated segment grid, marker positions and directed mapping IDs with the saved project and its RUC. [Generated layout workflow](skills/br-automation-studio/references/layout-svg-review.md)

Use `launchpad_export_review` to compare a locally decoded LaunchPad cycle with the XML export before import. Use `launchpad_route_review` to compare that cycle with the saved or compiled Traffic Manager. The native build can retain the reviewed route inputs and pin the saved segment grid and station and waypoint positions. [LaunchPad route workflow](skills/br-automation-studio/references/launchpad-route-review.md) · [Build with LaunchPad evidence](skills/br-automation-studio/references/native-build.md#bind-the-reviewed-launchpad-export-to-the-build)

Use `launchpad_station_review` to verify each station-to-station path and read the source station's LaunchPad model waits before reviewing the native recipe. [Station path workflow](skills/br-automation-studio/references/launchpad-station-review.md)

Use `station_trace_review` to compare a saved inspection release and arrival trace with the compiled station path. [Inspection trace workflow](skills/br-automation-studio/references/station-trace-review.md)

Use `cycle_trace_review` to check the release, return and next held request for the same shuttle on a compiled two-station loop. The review can verify the station motion inputs on the Release edge and compare the next hold with the HMI reopen from the same build and runtime session. For projects that export scans during simulation, the status smoke can retain the exact scan file before ARsim shuts down. [Inspection cycle workflow](skills/br-automation-studio/references/cycle-trace-review.md) · [Saved scan files](skills/br-automation-studio/references/cycle-scan-capture.md)

Use strict same-session scan review to take omitted scan and HMI identities from the status receipt. The configured held-status roles can also supply the binding map. [Status receipt binding](skills/br-automation-studio/references/cycle-trace-review.md#use-the-status-receipt-for-scan-identity)

Use `launchpad_recovery_review` to compare station recovery declarations and check the saved-to-compiled AS4 point sequence. [Recovery declaration workflow](skills/br-automation-studio/references/launchpad-recovery-review.md)

Use `launchpad_motion_review` to read raw LaunchPad exit parameters and check whether the compiled assembly retained or supplied its in-plane limits. [Motion parameter workflow](skills/br-automation-studio/references/launchpad-motion-review.md)

Use `launchpad_geometry_review` to compare station and waypoint distances and handedness in the saved or compiled manager. [LaunchPad geometry workflow](skills/br-automation-studio/references/launchpad-geometry-review.md)

Use `launchpad_layout_review` to compare the linked 240 mm S3 layout and grid offsets from its master. [LaunchPad segment workflow](skills/br-automation-studio/references/launchpad-layout-review.md)

Use `launchpad_frame_review` to compare route object positions under the same axis mappings that align the segment grids. [LaunchPad coordinate frame workflow](skills/br-automation-studio/references/launchpad-frame-review.md)

Use `launchPadWidgetId` in the native build requests to verify that the default mapp View page displays the reviewed Traffic Manager. [Assembly6D build and RUC check](skills/br-automation-studio/references/native-build.md#check-the-default-assembly6d-widget)

Use `verifyLaunchPadRules` to carry the selected manager's reviewed exclusivity groups into the build plan and RUC review. [Traffic rule check](skills/br-automation-studio/references/native-build.md#pin-the-reviewed-traffic-rules)

Use `verifyLaunchPadRecovery` to verify station recovery eligibility, mode and indexed points through compilation. [Station recovery check](skills/br-automation-studio/references/native-build.md#pin-the-reviewed-station-recovery)

Use `verifyLaunchPadMotionLimits` to verify the linked assembly's explicit in-plane limits and retain the raw LaunchPad exit values. [Motion limit check](skills/br-automation-studio/references/native-build.md#pin-the-explicit-in-plane-motion-limits)

Use `launchPadReadAxes` to require explicit read channels and verify the linked assembly's communication references. [Assembly I/O and read axes](skills/br-automation-studio/references/native-build.md#pin-the-assembly-io-and-read-axes)

Use `station_trace_review` to compare the recorded station motion inputs on the Release edge with an expected velocity and acceleration. [Release motion check](skills/br-automation-studio/references/station-trace-review.md#check-the-motion-inputs-on-the-release-edge)

Use `positionToleranceMicrometres` to compare the same shuttle's X and Y at outbound Release and arrival with the compiled station positions. Use `returnPositionToleranceMicrometres` in the cycle review to check return source occupancy, return arrival and the next hold. [Outbound positions](skills/br-automation-studio/references/station-trace-review.md#check-position-at-the-two-station-events) · [Return and hold positions](skills/br-automation-studio/references/cycle-trace-review.md#verify-positions-on-return-and-at-the-next-hold)

Use `waypointToleranceMicrometres` to check for ordered, same-shuttle samples near the compiled waypoints. [Waypoint observations](skills/br-automation-studio/references/station-trace-review.md#check-position-near-each-waypoint)

Use `verifySampledSpeed` to compare timed XY displacement rates with the compiled velocity limit. [Sampled in-plane speed](skills/br-automation-studio/references/station-trace-review.md#check-sampled-in-plane-speed)

Use `maxSampleIntervalMicroseconds` with the speed check to require a maximum time gap between usable XY samples. [Sampling interval check](skills/br-automation-studio/references/station-trace-review.md#limit-the-interval-between-position-samples)

Use `verifyPositionTimeHandoff` in the cycle review to compare acquisition times at the outbound-to-return handoff on a shared clock. [Timestamp handoff review](skills/br-automation-studio/references/cycle-trace-review.md#compare-timestamps-at-the-handoff-between-legs)

Use `scanCapture.holdThroughScan` to verify the next held request on each recorded PLC scan and check that the capture covers the HMI reopen. [Hold continuity review](skills/br-automation-studio/references/cycle-trace-review.md#verify-the-held-request-through-the-reopen)

Use `requireHmiAssemblyWidget` to pair the cycle with the Assembly6D startup widget and its rendered layout in both HMI documents. [Cycle widget review](skills/br-automation-studio/references/cycle-trace-review.md#verify-the-cycles-assembly6d-widget)

Use `hmiLayoutSvgReport` to match the geometry review to the SVGs captured by that HMI status run. [Cycle layout geometry review](skills/br-automation-studio/references/cycle-trace-review.md#match-the-captured-layout-geometry-to-the-cycle)

Use `verifyMappingLineEndpoints` in the SVG review to check the directed lines drawn between the configured markers. [Mapping endpoint review](skills/br-automation-studio/references/layout-svg-review.md#check-the-drawn-mapping-endpoints)

Use `verifyTrafficLabels` in the SVG review to check the name and anchor of each station and waypoint label. [Traffic label review](skills/br-automation-studio/references/layout-svg-review.md#check-station-and-waypoint-labels)

Use `ruc_binding_review` to trace a selected one-way mapp View status binding through the saved project, compiled widget and OPC UA exposure map. Set `verifyIecType` to compare the compiled node type with the selected CPU task's registered IEC declaration. [Binding readback workflow](skills/br-automation-studio/references/ruc-binding-review.md)

For a private loopback fixture, `arsim_smoke` materializes the reviewed RUC and compares served mapp View assets with their packaged bytes. [ARsim smoke workflow](skills/br-automation-studio/references/arsim-smoke.md)

With the optional `browser` extra and Google Chrome, `arsim_browser_smoke` verifies the Assembly6D widget and advancing PLC-bound counter across a page reopen. Use `requireStartPage` to verify startup placement and the generated client map. Use `requireWidgetBundle` to follow the packaged Assembly6D bundle through HTTP and both browser documents. Set `captureLayoutAssets` in the browser or combined HMI check to save the generated SVGs for offline review. [Headless browser workflow](skills/br-automation-studio/references/arsim-browser-smoke.md)

With the optional `opcua` extra, `arsim_opcua_smoke` reads an explicitly selected PLC counter directly from the reviewed ARsim instance and records the reported access levels and advancing values. [Direct OPC UA workflow](skills/br-automation-studio/references/arsim-opcua-smoke.md)

Use `arsim_hmi_smoke` with both extras to verify the binding and default visualization, then check that the displayed counter stays within direct OPC UA readings in one ARsim session. [Combined HMI workflow](skills/br-automation-studio/references/arsim-hmi-smoke.md)

Use `arsim_status_smoke` to verify expected numeric status such as a held request and shuttle in the initial and reopened HMI and in direct OPC UA. [Status reconnect workflow](skills/br-automation-studio/references/arsim-status-smoke.md)

Use the [one-shuttle rehearsal runbook](skills/br-automation-studio/references/inspection-cycle-rehearsal.md) to run the build, scan capture, HMI reopen and SVG review as one evidence set.

## Chat workflow

The repository includes a skill for selecting saved inputs and interpreting the inspection result. [B&R Automation Studio skill](skills/br-automation-studio/SKILL.md) · [Saved-project workflow](skills/br-automation-studio/references/project-inspection.md)

## Architecture

The application design covers the shared operation catalog, standalone CLI, chat skill, and the native build boundary. [Toolset architecture](docs/ARCHITECTURE.md)

## Maintainers

Project contact [votecoffee](https://github.com/votecoffee).

## Contributing

Use [GitHub Issues](https://github.com/votecoffee/BRAutomationStudioMCPServer/issues) for questions and reproducible findings. Submit proposed changes as pull requests with a description of the supported versions and the checks performed. Use synthetic or sanitized fixtures that you have permission to share.

## License

[MIT](LICENSE) © 2026 votecoffee.
