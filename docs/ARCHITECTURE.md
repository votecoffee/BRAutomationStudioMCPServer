# Toolset architecture

[Repository README](../README.md)

The standalone CLI exposes `project_inspect` for a saved project and `build_preflight` for a read-only comparison with an AS4 toolchain. Use ACOPOS 6D, Traffic Manager, and mapp View to exercise the design.

## Application layers

| Component | Responsibility |
| --- | --- |
| `src/br_automation_studio/` | Inspect saved snapshots, resolve references, and produce typed diagnostics and artifact metadata. Own one operation catalog with request and result types. |
| `src/br_automation_studio/cli.py` | Discover operations and schemas, accept a UTF-8 JSON request, and return structured JSON. Keep console formatting and exit codes at this boundary. |
| `src/br_automation_studio/assembly_io.py` and `global_variables.py` | Verify selected assembly I/O references against saved IEC declarations and compare the cyclic read-axis flags in the compiled configuration. |
| `src/br_automation_studio/preflight.py` | Compare saved selections and selected library dependencies with local AS4 catalogs. |
| `src/br_automation_studio/simulation_preflight.py` | Check a reviewed AS4 mapp 6D pair, exact package selection, simulator media and registration, and native LaunchPad export identity. |
| `src/br_automation_studio/native_build.py` and `windows_job.py` | Pin an installed AS4 toolchain and source tree, stage a private copy, run the native PC simulation build, and retain a job receipt with RUC evidence. |
| `src/br_automation_studio/links.py` | Resolve assemblies, traffic objects, mappings, linked exclusivity groups and widget references. |
| `src/br_automation_studio/ruc_review.py` | Bind a retained archive to a completed build and compare the saved and compiled links, routes and linked rules. |
| `scripts/decode-launchpad.cjs` and `src/br_automation_studio/launchpad_route.py` | Decode a known LaunchPad save locally, bind its readback to the source hash, and compare typed edges with a selected saved or compiled manager. |
| `src/br_automation_studio/launchpad_recovery.py` | Compare per-station recovery flags, mode and point counts against a matched LaunchPad route, and check indexed point positions within AS4. |
| `src/br_automation_studio/launchpad_stations.py` | Read station-to-station legs and the associated source station model processes in a matched LaunchPad cycle. |
| `src/br_automation_studio/launchpad_motion.py` | Read raw route-object exit parameters and identify explicit and build-materialized in-plane limits on the linked assembly. |
| `src/br_automation_studio/launchpad_geometry.py` | Compare positions in the selected route's saved or compiled object lists, reporting distances and a handedness witness. |
| `src/br_automation_studio/launchpad_layout.py` | Resolve the selected manager's linked layout and compare the occupied 240 mm grid cells relative to each input's master segment. |
| `src/br_automation_studio/launchpad_frame.py` | Compare station and waypoint residuals with a shared translation under the grid-compatible axis mappings. Accept an optional common datum bound to the inputs. |
| `src/br_automation_studio/binding_review.py` | Follow the saved and packaged visualization binding references for a selected mapp View widget. Verify the expected OPC UA source and read back its compiled node exposure. |
| `src/br_automation_studio/arsim_smoke.py` | Materialize an ARsim runtime, verify owned loopback listeners, and compare served HMI assets with the bound RUC. |
| `src/br_automation_studio/browser_smoke.py` | Run a headless Chrome job against the same verified ARsim HTTP session. Record widget, counter, and expected layout SVG evidence before and after a page reopen. |
| `src/br_automation_studio/opcua_smoke.py` | Read an explicit namespace URI and string node ID on the owned ARsim OPC UA listener. Record access metadata, data quality and counter samples. |
| `src/br_automation_studio/hmi_smoke.py` | Bind the compiled value binding and 6D reviews to the same package, then run the existing direct OPC UA and browser probes inside one ARsim job. |
| `skills/br-automation-studio/` | Give future chats a short entry point that locates the installed standalone executable and routes work by operation. |
| `skills/br-automation-studio/references/` | Maintain versioned workflow guides, request examples, and verification steps distributed with the CLI. |
| `tests/fixtures/` | Hold synthetic inputs and expected outcomes, including unresolved references and unsupported formats. |

Keep the core callable in process. A future MCP adapter can use the operation catalog for tool schemas and dispatch to the same functions. Project parsing, validation, and native job rules belong in the application layers above.

## Command and result contract

Use the Rockwell CLI's discovery pattern for the B&R CLI. The available commands are `operations`, `operation-help <operation>`, and `run --request <file>`. A request carries a schema version, workspace, operation, and typed arguments. Use the discovered schema to prepare the request.

A result identifies its operation, source snapshot, outcome, and diagnostics. Include exact input SHA-256 hashes, relevant tool versions, and hashes for any retained report or artifact. Include the checks performed and any unresolved or unsupported scope. Preserve the distinction between a check that passed, a check that failed, and a check that could not run. Use bounded output and make large collections pageable when needed.

Add durable job IDs when native work begins. Keep each job's input snapshot, selected tool, diagnostics, output hashes, and completion state together. A timeout may leave the native outcome unknown. Inspect the retained job before retrying. Record saved-file checks, native compilation, simulated behavior, and hardware acceptance as different evidence.

## First workflow

1. Open an explicitly selected saved `.apj` in a private workspace. Record its exact hash and track the relative paths and hashes of the project files actually read. Preserve unknown formats and report path or parse errors.
2. List project configurations and declared technology package versions. Use `build_preflight` to compare the same saved selections against an explicitly selected AS4 catalog.
3. For the selected configuration, inventory the ACOPOS 6D layout and assembly, Traffic Manager object and mapping lists, and mapp View content. Resolve their explicit links. Diagnose missing or mistyped mapping targets and a widget whose assembly and manager links disagree.
4. Produce a structured result and a readable report from the same findings. Include the selected configuration, checked links, unsupported objects, and source hashes. Validate the reporting with a synthetic project and a variant with an unresolved return edge. Verify the source bytes before and after the run.

A native build uses `build_plan` and `build_execute` with an installed AS4 toolchain and a private copy. The worker uses B&R's `BR.AS.Build.exe` to generate an offline RUC archive. The Automation Studio build result, generated module readback, ARsim connection, and browser behavior each provide their own evidence.

## Skill and installation workflow

The source skill and its workflow can be read from the checkout. For a future packaged release, publish the standalone executable, matching guides, and an application-owned skill as one versioned distribution. Use a stable per-user executable entry point. The skill should discover available operations, select the appropriate guide, verify supported inputs, and read receipts through the installed version. Keep detailed instructions in the skill's `references/` directory so a workflow has one maintained source.

Keep vendor installations and customer source paths in local settings. Keep raw exports, local corpus material, and artifacts out of tracked files. Use the ignored `.local/` directory for retained repo-specific material. Use synthetic fixtures for shareable examples. An installer should preserve existing local settings and support reverting to a verified previous version.

## Rockwell reference

The Rockwell MCP Server consultation pointed to the shared operation layer in `src/Studio5000.Core/EngineeringOperations.cs`, structured CLI dispatch in `src/Studio5000.EngineeringCli/WorkspaceRun.cs`, and native execution in `src/Studio5000.NativeBridge/SdkRunner.cs`. Its maintained standalone skill lives under `workflows/rockwell-engineering-cli/`. `scripts/Publish-EngineeringMiddleware.ps1` is the publishing example. The B&R project adopts the application and workflow boundaries first.
