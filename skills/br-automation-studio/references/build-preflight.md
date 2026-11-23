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

## Check the saved controller revision

With br-studio 0.93.0 or later, set `expectedControllerType` in a `build_preflight` request to require the exact controller model in the selected configuration's `Hardware.hw`. For example, set it to `8FLM11.0000.00I-2` for that ACOPOS 6D controller. The check reads each matching module's saved name and version and verifies the exact `AS/Hardware/Modules/<Type>/<Version>/<Type>.hwx` from the selected Studio catalog. [B&R controller hardware upgrades](https://www.br-automation.com/en-us/products/mechatronic-systems/acopos-6d/controller/8flm11000000i-2/)

Read `preflight.controllerHardware` for the saved revision, catalog path and catalog revision. The project and Studio read sets include the files used. A different saved model produces `controller_type_missing`. A missing exact catalog revision or mismatched XML revision produces a separate diagnostic. Use the active physical configuration for this review. Verify the assembly's controller assignment, POWERLINK node, I/O map and delivered hardware through the native configuration workflow.

### Check the saved 6D communication mapping

With br-studio 0.94.0 or later, add `controllerIoAssembly` with the assembly ID and keep `expectedControllerType` in the same preflight request. Use this check for the AS4 200-word communication array layout. It follows the saved assembly's Input and Output references to registered `Mc6DControllerFBIOType` variables and reads the selected CPU's registered `.iom` file. The selected configuration needs one matching controller for this comparison. [B&R hardware-specific configuration topic](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/50_making_hardware-specific_configuration.html)

Read `controllerIoReview` for the device, paths, variables and counts. The check compares each required array index, word access and channel ID against the exact catalog's SG4 input and output words. It identifies duplicates, missing indices and invalid rows. Task qualifiers are recorded as saved.

With br-studio 0.95.0 or later, set `controllerIoTaskClass` to the intended class number, from 1 through 8, in the same request. For example, `"controllerIoTaskClass": 1` requires every input and output row to carry `Cyclic#1.`. A complete map with mixed or missing qualifiers produces `controller_io_task_class_mismatch`.

Read `controllerIoReview.taskTiming` for the selected CPU's explicit `Cyclic<n>Duration` and `Cyclic<n>Tolerance` values in `Hardware.hw`. The report uses microseconds, matching the X20 CPU catalog fields. Omitted or ambiguous values are reported as `unverified` and produce a warning. Review effective defaults, POWERLINK timing and system timer source in Automation Studio. Use the native I/O Mapping view to confirm enabled channels. [B&R ACOPOS 6D timing configuration](https://help.br-automation.com/#/en/4/motion/mapp_6d/konfiguration/timing_konfiguration.html)

### Check the saved POWERLINK timing

With br-studio 0.96.0 or later, add `controllerPowerlinkConnector` and `expectedControllerType` to check a direct saved link from one 6D controller to the selected CPU. For example, use `"controllerPowerlinkConnector": "IF3"` for an X20CP1684 using its onboard POWERLINK port. The exact CPU and controller catalog revisions establish the supported connector names. The saved controller `Connection` supplies the destination CPU and port. [B&R X20CP1684 port specifications](https://www.br-automation.com/en-ca/products/plc-systems/x20-system/x20-plc/x20cp1684/) · [B&R saved 6D sample](https://github.com/br-automation-community/mapp6D-SampleProject/blob/891da6f6e64dca737406e1f301e581c464f9d310/mapp6d_SampleProject/Physical/Config1/Hardware.hw#L4-L25)

Read `controllerPowerlinkReview` for the matching controller port, the explicit `TimerDeviceType` and `TimerDevice`, and the CPU connector's `CycleTime` in microseconds. The CPU timer must select `EPLX2X` and name the selected interface. An explicit conflicting port, timer, operating mode or activation setting produces `controller_powerlink_mismatch`. An absent cycle value, unresolved direct connection or ambiguous selector produces `controller_powerlink_unverified`. Daisy chains and separate interface modules require native review.

When also checking `controllerIoTaskClass`, compare its saved duration and tolerance with the reported POWERLINK cycle using the applicable mapp 6D timing topic. Confirm effective port settings, synchronized system tick and observed task margin in Automation Studio. [B&R ACOPOS 6D timing configuration](https://help.br-automation.com/#/en/4/motion/mapp_6d/konfiguration/timing_konfiguration.html)

### Compare the saved assembly and hardware addresses

With br-studio 0.97.0 or later, add `controllerPowerlinkAssembly` with the registered assembly ID to the same POWERLINK request. The check compares the assembly's `Fieldbus6D/IFName` and `Fieldbus6D/NodeNumber` with the matched direct CPU connector and controller node in `Hardware.hw`. Supported node values are decimal controlled-node numbers from 1 through 239. The hardware node may be saved on the direct `Connection` or on its controller `Module`. When both supply a node, their values must agree. [B&R controller setup topic](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/40_adding_acopos_6d_controller.html)

Read `controllerPowerlinkReview.assemblyAddress` for the saved assembly path, interface and both node numbers. An explicit disagreement produces `controller_powerlink_address_mismatch`. A missing or ambiguous value produces `controller_powerlink_address_unverified`. Older saves can omit `IFName`, including B&R's sample. Check the effective interface in Automation Studio when the report is unverified, and confirm the controller's commissioned node number on the live segment. [B&R saved 6D assembly example](https://github.com/br-automation-community/mapp6D-SampleProject/blob/891da6f6e64dca737406e1f301e581c464f9d310/mapp6d_SampleProject/Physical/Config1/X20CP3586/mapp6D/Config_1.assembly6d#L47-L50)

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
