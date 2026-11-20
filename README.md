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

Use `simulation_environment` to check the AS4 package catalog, simulator registration and saved simulator media during workstation setup. Run `simulation_preflight` once the saved project and native LaunchPad export are available. [Simulation prerequisites and MpTraffic definition check](skills/br-automation-studio/references/simulation-preflight.md)

Use `build_plan` and `build_execute` to compile a pinned AS4 PC simulation project and collect the RUC archive and builder log. Build dispatch supports short Windows drive aliases for deeply nested jobs. [Native build and pinned MpTraffic workflow](skills/br-automation-studio/references/native-build.md)

After building, use `ruc_review` to compare the retained package's 6D modules, typed routes, linked exclusivity groups, and rendered widget links with the saved inputs. Supply `startPageWidgetId` to verify the default page placement of an Assembly6D widget. [RUC review workflow](skills/br-automation-studio/references/ruc-review.md) · [Traffic rule review](skills/br-automation-studio/references/traffic-rule-review.md)

Use `assembly_io_review` to trace the selected assembly's communication variable references to saved IEC declarations and check its compiled read-axis flags. [Assembly I/O workflow](skills/br-automation-studio/references/assembly-io-review.md)

Use `layout_device_review` to verify the selected CPU's saved and compiled `6DData` declaration before checking the runtime SVGs. [Layout file-device workflow](skills/br-automation-studio/references/layout-device-review.md)

Use `layout_svg_review` to compare the generated segment grid, marker positions and directed mapping IDs with the saved project and its RUC. [Generated layout workflow](skills/br-automation-studio/references/layout-svg-review.md)

Use `launchpad_route_review` to compare a locally decoded LaunchPad cycle with a selected saved or compiled Traffic Manager. [LaunchPad route workflow](skills/br-automation-studio/references/launchpad-route-review.md)

Use `launchpad_station_review` to verify each station-to-station path and read the source station's LaunchPad model waits before reviewing the native recipe. [Station path workflow](skills/br-automation-studio/references/launchpad-station-review.md)

Use `station_trace_review` to compare a saved inspection release and arrival trace with the compiled station path. [Inspection trace workflow](skills/br-automation-studio/references/station-trace-review.md)

Use `cycle_trace_review` to check the return and next held request for the same shuttle on a compiled two-station loop. Supply a completed `arsim_status_smoke` report to compare that hold with the HMI reopen from the same build and runtime session. For projects that export scans during simulation, the status smoke can retain the exact scan file before ARsim shuts down. [Inspection cycle workflow](skills/br-automation-studio/references/cycle-trace-review.md) · [Saved scan files, event codes and monitor checkpoints](skills/br-automation-studio/references/cycle-scan-capture.md)

Use `launchpad_recovery_review` to compare station recovery declarations and check the saved-to-compiled AS4 point sequence. [Recovery declaration workflow](skills/br-automation-studio/references/launchpad-recovery-review.md)

Use `launchpad_motion_review` to read raw LaunchPad exit parameters and check whether the compiled assembly retained or supplied its in-plane limits. [Motion parameter workflow](skills/br-automation-studio/references/launchpad-motion-review.md)

Use `launchpad_geometry_review` to compare station and waypoint distances and handedness in the saved or compiled manager. [LaunchPad geometry workflow](skills/br-automation-studio/references/launchpad-geometry-review.md)

Use `launchpad_layout_review` to compare the linked 240 mm S3 layout and grid offsets from its master. [LaunchPad segment workflow](skills/br-automation-studio/references/launchpad-layout-review.md)

Use `launchpad_frame_review` to compare route object positions under the same axis mappings that align the segment grids. [LaunchPad coordinate frame workflow](skills/br-automation-studio/references/launchpad-frame-review.md)

Use `ruc_binding_review` to trace a selected one-way mapp View status binding through the saved project, compiled widget, and OPC UA exposure map. [Binding readback workflow](skills/br-automation-studio/references/ruc-binding-review.md)

For a private loopback fixture, `arsim_smoke` materializes the reviewed RUC and compares served mapp View assets with their packaged bytes. [ARsim smoke workflow](skills/br-automation-studio/references/arsim-smoke.md)

With the optional `browser` extra and Google Chrome, `arsim_browser_smoke` verifies the Assembly6D widget and advancing PLC-bound counter across a page reopen. Use `requireStartPage` to verify startup placement and the generated client map. Use `requireWidgetBundle` to follow the packaged Assembly6D bundle through HTTP and both browser documents. Set `captureLayoutAssets` in the browser or combined HMI check to save the generated SVGs for offline review. [Headless browser workflow](skills/br-automation-studio/references/arsim-browser-smoke.md)

With the optional `opcua` extra, `arsim_opcua_smoke` reads an explicitly selected PLC counter directly from the reviewed ARsim instance and records the reported access levels and advancing values. [Direct OPC UA workflow](skills/br-automation-studio/references/arsim-opcua-smoke.md)

Use `arsim_hmi_smoke` with both extras to verify the binding and default visualization, then check that the displayed counter stays within direct OPC UA readings in one ARsim session. [Combined HMI workflow](skills/br-automation-studio/references/arsim-hmi-smoke.md)

Use `arsim_status_smoke` to verify expected numeric status such as a held request and shuttle in the initial and reopened HMI and in direct OPC UA. [Status reconnect workflow](skills/br-automation-studio/references/arsim-status-smoke.md)

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
