# Build an AS4 ARsim package

Use `build_plan` and `build_execute` for a trusted saved project with an English AS4 installation, a shared AS4 Runtime catalog, and a selected configuration with one `PC_any` CPU. The workflow compiles for ARsim and creates an offline RUC archive. This scope matches the native test configuration.

## Plan the build

Create the same request as for [build preflight](build-preflight.md) with `operation` set to `build_plan`. Run `br-studio run --request <plan-request.json>` and inspect `result.buildPlan` plus the diagnostics. If `buildPlan` has a SHA-256, the manifest identifies the complete staged input tree and the reported Studio and Runtime read sets. The plan preserves the on-disk spelling of source paths. The root folders `Binaries`, `Temp`, `BuildOnly`, `TempOnly`, `.git` and `.local` are excluded from the stage.

Review the selected project, configuration, builder and diagnostics. Planning is read-only. It stops on preflight errors and can return a plan with warnings. Use a self-contained project copy whose dependencies and build behavior you trust. The worker invokes the vendor build, including any build behavior configured in the project.

### Bind the reviewed LaunchPad export to the build

With br-studio 0.51.0 or later, use the same `launchPadProject`, `launchPadReadback` and `launchPadExport` inputs from [export review](launchpad-route-review.md#compare-the-export-before-import) and add the imported `managerId` to both build requests. Supply `nameMap` if the imported objects were renamed. The plan checks that the export matches its save and that the selected saved manager has the same named, typed objects and directed edges. The reviewed input hashes become part of the build plan SHA-256.

Before dispatch, execution retains and rehashes the exact evidence under the job directory's `launchpad/` subdirectory. Keep that directory with the plan, job receipt and RUC. The [RUC review](ruc-review.md) rehashes the retained files and checks the compiled manager's route. Automation Studio's native import and the saved geometry, process and runtime checks have their own review steps.

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
