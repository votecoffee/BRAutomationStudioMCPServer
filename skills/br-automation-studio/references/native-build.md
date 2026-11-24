# Build an AS4 ARsim package

Use `build_plan` and `build_execute` for a trusted saved project with an English AS4 installation, a shared AS4 Runtime catalog, and a selected configuration with one `PC_any` CPU. The workflow compiles for ARsim and creates an offline RUC archive. This scope matches the native test configuration.

## Prepare the first matched simulation build

Use the three requests below from one run directory. Adjust the Automation Studio and Runtime roots, saved project filename, configuration, CPU, selectors, and route and widget IDs to the reviewed target. The workstation example uses proposed AS4 2.7 selections. Update those fields when the exact vendor package builds are known. Keep the saved LaunchPad project, decoded readback and native export in `launchpad/`, the complete saved project in `source-project/`, the verified ZIPs in `media/` and the JSON responses in `receipts/`.

| Request | Save its response | Review before continuing |
| --- | --- | --- |
| [Workstation check](../../../examples/inspection-cycle/workstation-check.json) | `receipts/workstation.json` | The exact three-package selection, simulator registration and the four media roles. |
| [Saved-project preflight](../../../examples/inspection-cycle/simulation-preflight.json) | `receipts/simulation-preflight.json` | The project, CPU and Runtime, matched workstation receipt, native export and selected IEC declarations and MpTraffic definition. |
| [Native build plan](../../../examples/inspection-cycle/build-plan.json) | `receipts/build-plan.json` | The plan SHA-256 and its saved-project preflight, workstation, route, widget and Traffic Manager display, 6DData device, X/Y read axes, assembly limits and motion pins. |

Copy the examples into the run root as `workstation-request.json`, `simulation-preflight-request.json` and `build-plan-request.json`. Run each stage with `br-studio run --request <request.json>` and retain the JSON response before selecting the next stage. Use the same saved project, CPU, native export and workstation receipt for preflight and planning. A blocked workstation or preflight calls for resolving the reported dependency. Review the diagnostics when the preflight reports `review_required` or the planner returns a plan with warnings. The plan can carry the selected IEC declarations, the six motion binaries and generated motion globals into package verification. [Workstation and project prerequisites](simulation-preflight.md)

### Derive the execute request

After reviewing the plan and its diagnostics, run the following in PowerShell 7 from the same directory as `build-plan-request.json`. Use an unused job directory outside the project and installation roots. Keep the request, plan response and original sources together.

```powershell
$request = Get-Content -Raw -LiteralPath ./build-plan-request.json | ConvertFrom-Json -AsHashtable
$receipt = Get-Content -Raw -LiteralPath ./receipts/build-plan.json | ConvertFrom-Json -AsHashtable
$planHash = $receipt.result.buildPlan.sha256
if ($request.operation -ne 'build_plan' -or $planHash -cnotmatch '^[0-9a-f]{64}$') {
    throw 'Select a reviewed build_plan request and its plan receipt'
}
$request.operation = 'build_execute'
$request.arguments.planSha256 = $planHash
$request.arguments.jobDirectory = 'build-job'
$request.arguments.timeoutSeconds = 600
$request | ConvertTo-Json -Depth 20 |
    Set-Content -LiteralPath ./build-execute-request.json -Encoding utf8NoBOM
```

Run `br-studio run --request ./build-execute-request.json` and save its JSON response. The tool recalculates the plan before dispatch. Read `result.nativeBuild.status`, the compiler summary and the verified RUC checks. Keep the resulting job directory for package review and the HMI rehearsal.

For `verifyMotionSymbols`, preserve the exact generated files named in `job.json` under `motionSymbols[].path`. Keep their paths relative to the copied job, including files under `temporary/Includes`. Package review compares these declarations with the job receipt. [Replay a relocated run](inspection-cycle-rehearsal.md#replay-a-relocated-run)

The first-build examples exercised the blocked workstation and preflight responses and stopped at planning. A separate installed AS4.12 mapp 6D 2.2 control compiled with all six motion binaries and seven generated globals verified. Its archived RUC review passed with the generated files present. Separate receipt controls caught a missing file and a byte change to a generated declaration.

After reviewing the RUC, carry its plan and archive hashes into the [first HMI and SVG rehearsal](arsim-hmi-smoke.md#rehearse-the-first-reviewed-build).

## Plan the build

Create the same request as for [build preflight](build-preflight.md) with `operation` set to `build_plan`. Run `br-studio run --request <plan-request.json>` and inspect `result.buildPlan` plus the diagnostics. If `buildPlan` has a SHA-256, the manifest identifies the complete staged input tree and the reported Studio and Runtime read sets. The plan preserves the on-disk spelling of source paths. The root folders `Binaries`, `Temp`, `BuildOnly`, `TempOnly`, `.git` and `.local` are excluded from the stage.

Review the selected project, configuration, builder and diagnostics. Planning is read-only. It stops on preflight errors and can return a plan with warnings. Use a self-contained project copy whose dependencies and build behavior you trust. The worker invokes the vendor build, including any build behavior configured in the project.

### Pin the Assembly6D layout device

With br-studio 0.124.0 or later, use `verifyLayoutDevice` in both build requests for an ARsim project with `6DData` explicitly declared on `USER_PATH` in the selected CPU's `Hardware.hw`. Planning records the device slot and target in `buildPlan.manifest.layoutDevice`. Execution verifies the `/DEVICE=USER_PATH` parameter in the RUC system configuration. A successful job records the device and system configuration hash in `nativeBuild.package.layoutDevice`. RUC review repeats the check against the saved project and job. [Layout device review](layout-device-review.md)

### Keep the workstation package review with the build

With br-studio 0.90.0 or later, pass the same `environmentReport` in `build_plan` and `build_execute` that you used for the [saved-project simulation preflight](simulation-preflight.md#carry-the-workstation-package-review-into-the-saved-project). The plan rechecks the three saved selectors, installed builds and manifest hashes and includes the matched review and its receipt SHA-256 in `buildPlan.manifest.environmentReview`.

Build execution repeats the comparison and requires the same plan SHA-256. It retains the exact JSON as `simulation-environment.json` in the job directory, records its hash in `job.json`, and rechecks the installed read set before dispatch. Keep that file with the plan, job receipt and RUC. RUC review rehashes the retained file when the plan includes this evidence.

### Keep the saved-project simulation preflight with the build

With br-studio 0.122.0 or later, pass the JSON response from `simulation_preflight` as `simulationPreflightReport` in both build requests, together with the same `environmentReport` and LaunchPad route inputs. The plan checks the selected project, configuration, CPU, reviewed Studio and Runtime files, the project files read during preflight, and the SHA-256 of the reviewed workstation report and native `.x6d`. The plan SHA-256 also covers the full source tree staged for compilation.

Review any warnings on an `inputs_present` or `review_required` preflight and look for `buildPlan.manifest.simulationPreflightReview` in the plan. Build execution retains the exact receipt as `simulation-preflight.json` before dispatch. Keep it with `plan.json`, `job.json` and the RUC. RUC review verifies its SHA-256 and returns it as `rucReview.simulationPreflightReport`. Review simulator registration and behavior separately when running the package.

### Bind the reviewed LaunchPad export to the build

With br-studio 0.51.0 or later, use the same `launchPadProject`, `launchPadReadback` and `launchPadExport` inputs from [export review](launchpad-route-review.md#compare-the-export-before-import) and add the imported `managerId` to both build requests. Supply `nameMap` if the imported objects were renamed. The plan checks that the export matches its save and that the selected saved manager has the same named, typed objects and directed edges. The reviewed input hashes become part of the build plan SHA-256.

Before dispatch, execution retains and rehashes the exact evidence under the job directory's `launchpad/` subdirectory. Keep that directory with the plan, job receipt and RUC. The [RUC review](ruc-review.md) rehashes the retained files and checks the compiled manager's route. Automation Studio's native import and the saved geometry, process and runtime checks have their own review steps.

### Pin the imported segment grid

With br-studio 0.52.0 or later, add `verifyLaunchPadLayout: true` to both build requests along with the LaunchPad route inputs above. The option follows the selected manager through its assembly to one registered 240 mm S3-AS layout. The [layout comparison](launchpad-layout-review.md) checks the occupied cells relative to the master against the saved LaunchPad design, allowing the compatible square-grid axis mappings.

The plan records the imported layout ID, segment count, master grid position and occupied offsets in the selected Automation Studio coordinate frame. RUC review checks that same grid in the compiled layout. Read `rucReview.launchPadRoute.layout` for the planned and compiled values. Use the [coordinate frame review](launchpad-frame-review.md) to check the route positions and common datum.

### Pin the reviewed traffic positions

Run the [coordinate frame review](launchpad-frame-review.md) on the imported geometry first. With br-studio 0.53.0 or later, add `verifyLaunchPadPositions: true` to both build requests with the reviewed route inputs. The plan records the saved Absolute X and Y values in metres for every station and waypoint in the named manager. Set `verifyLaunchPadLayout` as well to pin the segment grid.

[RUC review](ruc-review.md) compares the compiled X and Y numerically for each reviewed object. Equivalent decimal spellings match. Inspect the planned values, compiled values and moved object names under `rucReview.launchPadRoute.positions`. This verifies coordinates in the saved Automation Studio frame. Use the frame review to qualify the LaunchPad axis mapping and datum.

### Check the default Assembly6D widget

With br-studio 0.54.0 or later, add `"launchPadWidgetId": "Overview_PlanarDisplay"` to both build requests with the reviewed route inputs. Use the exact `ContentId_WidgetId` from the saved project. Planning checks that this Assembly6D widget is placed on the default visualization's start page and that its links point to the reviewed manager and that manager's assembly. The selected widget ID is part of the build plan SHA-256.

[RUC review](ruc-review.md#verify-the-default-page-placement-of-assembly6d) reads the compiled links, client map, and rendered content for the pinned widget automatically. The [browser rehearsal](arsim-hmi-smoke.md) uses the pinned widget and checks the served client map in both browser documents. Enable `captureLayoutAssets` and run [SVG review](layout-svg-review.md) to compare the served geometry.

With br-studio 0.125.0 or later, add `"requireTrafficDetails": true` to both build requests with the reviewed LaunchPad route. Select `launchPadWidgetId` when several saved Assembly6D widgets exist. The plan verifies that the widget's saved `showAdditionalTrafficManagerInfo` option or registered widget default is enabled. Read its source under `buildPlan.manifest.launchPadRoute.trafficDetails`. The build verifies the generated Boolean `true` in the selected startup content before accepting the RUC and records it in `nativeBuild.package.trafficDetails`. [Review the generated display option](ruc-review.md#verify-the-generated-traffic-manager-display-option)

### Pin the reviewed traffic rules

Review the saved manager's linked rule lists and typed member, entry and exit objects. With br-studio 0.55.0 or later, add `"verifyLaunchPadRules": true` to both build requests with the reviewed route inputs. The plan records the selected manager's readable lists and exclusivity groups. An empty list is recorded with zero groups. An unresolved link or unsupported rule format prevents planning. [Traffic rule review](traffic-rule-review.md)

RUC review checks the same lists and role assignments. Read `rucReview.launchPadRoute.rules` for the planned and compiled groups and changed list IDs. Review the target simulator's booking and release sequence with the intended routes and shuttles.

### Pin the reviewed station recovery

With br-studio 0.56.0 or later, use `"verifyLaunchPadRecovery": true` in both build requests with the reviewed route inputs. Planning compares each imported station's saved recovery mode, station eligibility flag and explicit point count with the LaunchPad declarations. It records the model-wide starting-position flags and the saved Automation Studio points by index with Absolute X and Y in metres. [Station recovery review](launchpad-recovery-review.md)

RUC review compares the compiled mode, eligibility and indexed positions with the plan. Read `rucReview.launchPadRoute.recovery` for the planned and compiled declarations and the stations that changed. Check the target runtime for actual shuttle occupancy after recovery.

### Pin the explicit in-plane motion limits

With br-studio 0.57.0 or later, add `"verifyLaunchPadMotionLimits": true` to both build requests with the reviewed LaunchPad route inputs. Review and explicitly declare positive `InPlaneVelLim` and `InPlaneAccLim` values on the assembly linked by the selected manager. The plan records both values and the raw LaunchPad exit velocity, acceleration and payload weight for each route object. Use [motion readback](launchpad-motion-review.md) to examine the values supplied by a build when a saved field is absent.

RUC review requires the explicit saved limits to survive compilation. Read `rucReview.launchPadRoute.motionLimits` for the plan, compiled fields and per-field comparisons. Capture the actual station release inputs and observed motion in the target simulator.

### Pin the assembly I/O and read axes

With br-studio 0.60.0 or later, add `"launchPadReadAxes": ["X", "Y"]` to both build requests with the reviewed LaunchPad route inputs. Select the axes needed by the capture from X, Y, Z, Rx, Ry and Rz. The assembly linked by the selected manager must explicitly enable each selected read axis. Its Input and Output references must resolve to `Mc6DControllerFBIOType` variables in the registered root `Logical/Global.var`. [Assembly I/O readback](assembly-io-review.md)

The plan records both typed references and the saved read-axis declarations. RUC review compares both I/O references and every explicitly saved flag, including explicit `FALSE` values. Read `rucReview.launchPadRoute.assemblyIo` for planned and compiled fields and their comparisons. Use the target simulator to verify actual channel mapping and live axis values.

### Verify MpTraffic in the plan and RUC

Since br-studio 0.35.0, set `verifyMpTrafficEnum` to true in both the plan and execution requests to carry the [MpTraffic definition check](simulation-preflight.md#verify-the-mptraffic-definition-before-building) into the native build. The plan compares the registered project declaration and IA32 binary with the selected AS4 catalog files. The plan SHA-256 covers the saved and installed declaration and payload hashes under `manifest.mpTrafficDefinition`.

Execution repeats the comparison and rehashes the installed files after staging and before running the builder. A missing option or a changed pinned file, including a formatting-only edit to a declaration, requires a fresh reviewed plan. A completed build also needs the same MpTraffic binary in the RUC to pass package verification. Read `nativeBuild.package.mpTrafficPayload` for its member path, SHA-256 and size.

### Verify the motion payloads in the RUC

With br-studio 0.41.0 or later, set `verifyMotionPayloads` to `true` on both `build_plan` and `build_execute`. The request pins `McAcp6D`, `MpTraffic`, `McBase`, `MpBase`, `McAcp6DSys` and `McDriveLog` for the selected SG4/IA32 PC simulation. Use the same options when executing the plan.

The plan records each payload's origin, path, and SHA-256. The supported inputs include project Libraries registered in the saved logical tree and selected by the technology package or CPU, and explicit library or module versions from the selected catalog. A project copy of `McDriveLog.br` is resolved from the selected CPU's BinaryObject and its registered Logical file. When both the project and the selected catalog provide a library, their SG4/IA32 bytes must agree. The staged project and selected catalog files are rechecked before dispatch.

The build needs one exact copy of each pinned payload under `FilesToTransfer/Data0` in its RUC. The successful job records all six package paths, sizes, and hashes. Use [RUC review](ruc-review.md) to recheck that evidence from a retained build. `verifyMpTrafficEnum` can be used with the motion pins to check the selected MpTraffic event declarations.

### Carry the IEC declaration hashes into the build

Set `verifyMotionDeclarations` to `true` on both `build_plan` and `build_execute` to include the [McAcp6D and MpTraffic declaration review](build-preflight.md#verify-mapp-6d-iec-declarations) in the plan. The build records the selected catalog and project evidence in `manifest.motionDeclarations`. The existing staged source and catalog checks rehash the declaration files before dispatch. This option can be used with both `verifyMotionPayloads` and `verifyMpTrafficEnum`.

### Verify the generated assembly and Traffic Manager globals

With br-studio 0.46.0 or later, set `verifyMotionSymbols` to `true` on both `build_plan` and `build_execute`. The plan records the expected IEC names and types under `manifest.motionSymbols`, based on the selected CPU's registered assemblies, Traffic Managers and linked station and waypoint objects.

When the builder completes, the CLI checks `Mc6DElements.var` and, for a Traffic Manager, `TrafficElements.var` under `temporary/Includes/AS_TempDecl/<configuration>/GlobalComponents`. A missing name, duplicate symbol or unexpected type fails artifact verification. The receipt records the original declaration paths, hashes and source lines under `nativeBuild.motionSymbols`. Keep those exact files with `plan.json`, `job.json`, the build log and the RUC. [Retained RUC workflow](ruc-review.md)

Use the generated names when assigning the inputs of the assembly and station function blocks. Read the verified library's [public pin and type declarations](build-preflight.md#read-selected-function-block-pins) to confirm how each generated name is passed to the selected function block.

## Run the pinned copy

Create a request using the same arguments as the plan. Below, replace the example hash with `result.buildPlan.sha256` and choose an unused `jobDirectory` under an existing parent outside the input roots.

```json
{
  "schemaVersion": 1,
  "workspace": "arsim-package",
  "operation": "build_execute",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "configuration": "ARsim",
    "cpu": "PC",
    "studioRoot": "C:/Program Files/BRAutomation4/AS412",
    "planSha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "jobDirectory": "C:/BuildJobs/example-arsim-001",
    "timeoutSeconds": 180
  }
}
```

Run `br-studio run --request <build-request.json>`. The command regenerates the plan, checks the supplied hash, copies and rehashes the planned source files, then rechecks the installation read sets. The vendor command is `BR.AS.Build.exe` with `-c`, `-simulation`, `-all`, `-o`, `-t` and `-buildRUCPackage`. It runs from the staged project and writes to the job's separate binary and temporary directories. A changed plan or existing job directory blocks dispatch.

For a deep Windows workspace, you can map its existing directory to an unused drive letter with `subst` and supply the short drive path in `jobDirectory`. The tool checks the resolved path against the protected input roots and confirms the alias still identifies the staged job directory before launching the builder. The receipt records the resolved job directory and the actual native arguments. Keep the drive mapping in place until the build exits.

## Read the receipt

| File or field | Meaning |
| --- | --- |
| `plan.json` | The accepted manifest and SHA-256. |
| `job.json` | Job ID, command, state, build summary, cleanup result, and hashes of the log and verified package. |
| `builder.log` | Raw output from the builder and its process tree. |
| `binary/<configuration>/<cpu>/RUCPackage/RUCPackage.zip` | The generated archive. A successful job includes its SHA-256, member count, and ZIP integrity result. |

Read `result.nativeBuild.status` and the reported build error and warning counts. For example, B&R exit code **1** with a warnings-only summary and valid RUC yields a successful native job with partial CLI status. The CLI preserves the [shared exit codes](project-inspection.md#read-the-result). The RUC integrity check verifies the archive. Use [RUC review](ruc-review.md) to compare the compiled 6D configuration and HMI links with the saved inputs.

The worker joins a Windows Job Object before starting the vendor process. It checks for remaining processes when the build finishes. Timeouts and interrupted dispatches can leave an unknown build outcome. Inspect the retained `job.json` and `builder.log` before retrying. A new submission has its own directory and identity.

For mapp View warnings 9232 and 9233, compare the saved library registration and immediate directory entries using [widget package review](widget-package-review.md). Read the native copy steps and the widget files in the RUC when assessing those entries.

The workflow produces saved native build evidence. ARsim execution, traffic behavior, browser readback, hardware transfer, and commissioning need their own acceptance.
