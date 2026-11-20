# Build an AS4 ARsim package

Use `build_plan` and `build_execute` for a trusted saved project with an English AS4 installation, a shared AS4 Runtime catalog, and a selected configuration with one `PC_any` CPU. The workflow compiles for ARsim and creates an offline RUC archive. This scope matches the native test configuration.

## Plan the build

Create the same request as for [build preflight](build-preflight.md) with `operation` set to `build_plan`. Run `br-studio run --request <plan-request.json>` and inspect `result.buildPlan` plus the diagnostics. If `buildPlan` has a SHA-256, the manifest identifies the complete staged input tree and the reported Studio and Runtime read sets. The plan preserves the on-disk spelling of source paths. The root folders `Binaries`, `Temp`, `BuildOnly`, `TempOnly`, `.git` and `.local` are excluded from the stage.

Review the selected project, configuration, builder and diagnostics. Planning is read-only. It stops on preflight errors and can return a plan with warnings. Use a self-contained project copy whose dependencies and build behavior you trust. The worker invokes the vendor build, including any build behavior configured in the project.

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

The workflow produces saved native build evidence. ARsim execution, traffic behavior, browser readback, hardware transfer, and commissioning need their own acceptance.
