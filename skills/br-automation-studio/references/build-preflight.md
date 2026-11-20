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

## Verify mapp 6D IEC declarations

With br-studio 0.42.0 or later, set `verifyMotionDeclarations` to `true` in a `build_preflight` or `simulation_preflight` request. The check resolves the selected mapp 6D package's `McAcp6D` and `MpTraffic` descriptors and their listed `.fun`, `.typ` and `.var` files. When a library is registered in the saved project's Libraries package, its local descriptor must select the same version and list the same IEC files with matching bytes. When a library is selected from catalog alone, the result records its declared file hashes.

Check `result.motionDeclarations` for the mapp 6D selector, internal build, library states, paths and hashes. Differences in membership, whitespace or encoding can change the exact-byte comparison. The check covers IEC declarations for the selected package. Use the [motion payload pins](native-build.md#verify-the-motion-payloads-in-the-ruc) for the packaged binaries.

## Interpret the evidence

| Check | What the report establishes |
| --- | --- |
| Studio build tools | The reported `BR.AS.Build.exe` files exist and are hashed. On Windows, the preflight reads the AS build version from `BR.AS.BuildBase.dll` without running either file. The build wrapper's own version can differ from the AS build. |
| Technology package | The project selector must match the directory and `TP.cfg` identity under `AS/TechnologyPackages`. For example, `2.2.0` is the selector and `2.2.0.9` is the internal package build. `catalogDirectories` is an inventory of candidate folders. |
| Component selection and constraints | Versioned library descriptors and module payload candidates are located from the matching manifest. Declared numeric Studio and package ranges and lockouts are evaluated where the other version is known. Transfer modules such as `mvLoader` can expose a payload path without enough metadata to verify the selected component version. AR compatibility ranges are reported as declared. |
| Libraries | The preflight checks dependencies for descriptors read from selected technology-package libraries and explicit `Cpu.sw` library references in the supported `Package.Name.lby` form. It reports missing providers or selected versions outside the descriptor's range. A saved or installed library file needs a supported selected reference to count as a provider. |
| CPU and Runtime | The hardware result lists catalog entries matching the selected CPU module ID. The Runtime result identifies its AS4 catalog marker. |

`package_version_missing` is an exact-selection finding. A package from another release, an extracted installer, or copied project libraries will not satisfy a missing entry in the selected installed catalog. If the marker file is absent or inconsistent, keep its diagnostic and verify the installed package before dispatching a build.

The result includes separate SHA-256 read sets for the saved project, the selected Studio installation, and the Runtime catalog. Module entries can include payload candidates observed by path. Those binaries are outside the read-set hash unless the report lists them under `snapshot.files`. Keep receipts that contain local installation details in ignored `.local/` or a project-owned folder.

The exit codes are shared with the [saved-project workflow](project-inspection.md#read-the-result). The preflight reports catalog and saved-file evidence. Complete dependency closure, compiler output, simulation, licensing, and hardware behavior need their own verification.
