# Check a saved project against an AS4 installation

Use `build_preflight` when opening a saved project on a different engineering workstation or before asking Automation Studio to build it. Start with the saved `.apj`, its matching project tree, and the exact Automation Studio installation root under review.

## Run the preflight

Discover the installed request contract with `br-studio operation-help build_preflight`. Write a UTF-8 request without a BOM, then use `br-studio run --request <file>`. Add `--format markdown` for a readable report. Paths may be absolute or relative to the JSON request.

```json
{
  "schemaVersion": 1,
  "workspace": "as4-build-review",
  "operation": "build_preflight",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "configuration": "ARsim",
    "cpu": "PC",
    "studioRoot": "C:/Program Files/BRAutomation4/AS412"
  }
}
```

On the observed AS4 layout, the shared Runtime catalog is the sibling `AS/System` tree under the parent of `studioRoot`. Set `runtimeRoot` to the exact `AS/System` catalog path if the installation uses another location. A selected version such as `J4.93` maps to `J0493/SG4/Config.xml`. This confirms a catalog record. CPU-specific Runtime image selection remains a native check.

## Check package Runtime requirements

With br-studio 0.86.0 or later, preflight compares the selected CPU's saved Automation Runtime with the Runtime dependencies and lockouts declared in each selected package's installed `TP.cfg`. The result appears under the corresponding package in Markdown and in `preflight.technologyPackages[].constraints` in JSON. The receipt records the manifest path, the inclusive bounds, `selectedVersion` and `state`.

The comparison orders AS4 versions by the numeric release and then by the letter revision within that release. For example, `J4.93` meets a `C4.90` minimum and falls below a `K4.93` minimum. `version_out_of_range` flags an unmet dependency. `package_lockout` flags a Runtime inside an excluded range. Either error blocks the saved-project simulation preflight and native build plan. An unreadable bound is `unverified` and produces a warning when a CPU is selected. [B&R Automation Runtime 4.93 revision catalog](https://www.br-automation.com/en-us/products/software/automation-runtime/automation-runtime-versions/1swaremb493/)

## Verify mapp 6D IEC declarations

With br-studio 0.42.0 or later, set `verifyMotionDeclarations` to `true` in a `build_preflight` or `simulation_preflight` request. The check resolves the selected mapp 6D package's `McAcp6D` and `MpTraffic` descriptors and their listed `.fun`, `.typ` and `.var` files. When a library is registered in the saved project's Libraries package, its local descriptor must select the same version and list the same IEC files with matching bytes. When a library is selected from catalog alone, the result records its declared file hashes.

Check `result.motionDeclarations` for the mapp 6D selector, internal build, library states, paths and hashes. Differences in membership, whitespace or encoding can change the exact-byte comparison. The check covers IEC declarations for the selected package. Use the [motion payload pins](native-build.md#verify-the-motion-payloads-in-the-ruc) for the packaged binaries.

## Read selected function-block pins

Use `motion_interface_review` with the same project, catalog and CPU selection and a list of up to 32 function-block names. The operation checks the declaration evidence before reading public `VAR_INPUT`, `VAR_OUTPUT` and `VAR_IN_OUT` declarations from the selected `McAcp6D.fun` and `MpTraffic.fun` files.

```json
{
  "schemaVersion": 1,
  "workspace": "review-motion-interface",
  "operation": "motion_interface_review",
  "arguments": {
    "project": "Example/Example.apj",
    "studioRoot": "C:/Program Files/BRAutomation4/AS412",
    "configuration": "ARsim",
    "cpu": "PC",
    "blocks": ["MC_BR_AsmReadInfo_Acp6D", "MpTrafficStationMonitor"]
  }
}
```

Run `br-studio run --request <request.json> --format markdown` for the pin tables. The JSON includes the selected mapp 6D package build, library versions, source path and hash, and declaration lines. Multiple or missing definitions and unsupported syntax produce diagnostics. The report covers the named public formals in the supported AS4 `.fun` format. Use the exact selected package for nested type fields and runtime semantics.

### Trace nested STRUCT members

With br-studio 0.44.0 or later, add `fieldPaths` to the `motion_interface_review` request to resolve selected nested members. Each path starts with a block included in `blocks` and follows the public pin and named fields. For example, `["MpTrafficStationMonitor.DetectedShuttle.Shuttle.ShuttleID"]` reads the declaration chain for the detected shuttle ID.

The result includes the type and source line of each hop through the verified `.fun` and `.typ` files. A `missing` path identifies a field omitted from an inspected declaration. An `unverified` path identifies a chain the tool could not resolve, including ambiguous types and indirect references. The check follows directly declared `STRUCT` members.

### Read explicit enumeration values

With br-studio 0.45.0 or later, add `enumTypes` to `motion_interface_review` to read exact names and decimal values from the verified `.typ` files. For example, `["MpTrafficShDetectionEnum"]` selects the detection enum. Omit `blocks` for an enum-only request. For a pin or nested field, the typed pin or field report identifies the enum type to request.

The report retains each declaration file hash and member's source line. An ambiguous declaration, duplicate value, implicit value or unsupported expression leaves the enum unverified. Keep the selected package identity with the reported map. Use [the compiled scan review](cycle-scan-capture.md#decode-events-from-the-saved-project-and-ruc) to verify a numeric event capture against its saved project and retained RUC.

## Interpret the evidence

| Check | What the report establishes |
| --- | --- |
| Studio build tools | The reported `BR.AS.Build.exe` files exist and are hashed. On Windows, the preflight reads the AS build version from `BR.AS.BuildBase.dll` without running either file. The build wrapper's own version can differ from the AS build. |
| Technology package | The project selector must match the directory and `TP.cfg` identity under `AS/TechnologyPackages`. For example, `2.2.0` is the selector and `2.2.0.9` is the internal package build. `catalogDirectories` is an inventory of candidate folders. |
| Component selection and constraints | Versioned library descriptors and module payload candidates are located from the matching manifest. Declared Studio, Runtime and package ranges and lockouts are evaluated where the selected version can be compared. Transfer modules such as `mvLoader` can expose a payload path without enough metadata to verify the selected component version. |
| Libraries | The preflight checks dependencies for descriptors read from selected technology-package libraries and explicit `Cpu.sw` library references in the supported `Package.Name.lby` form. It reports missing providers or selected versions outside the descriptor's range. A saved or installed library file needs a supported selected reference to count as a provider. |
| CPU and Runtime | The hardware result lists catalog entries matching the selected CPU module ID. The Runtime result identifies its AS4 catalog marker. |

`package_version_missing` is an exact-selection finding. A package from another release, an extracted installer, or copied project libraries will not satisfy a missing entry in the selected installed catalog. If the marker file is absent or inconsistent, keep its diagnostic and verify the installed package before dispatching a build.

The result includes separate SHA-256 read sets for the saved project, the selected Studio installation, and the Runtime catalog. Module entries can include payload candidates observed by path. Those binaries are outside the read-set hash unless the report lists them under `snapshot.files`. Keep receipts that contain local installation details in ignored `.local/` or a project-owned folder.

The exit codes are shared with the [saved-project workflow](project-inspection.md#read-the-result). The preflight reports catalog and saved-file evidence. Complete dependency closure, compiler output, simulation, licensing, and hardware behavior need their own verification.
