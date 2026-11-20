---
name: br-automation-studio
description: Inspect saved B&R Automation Studio projects with the standalone br-studio CLI. Use for declared package versions, ACOPOS 6D assembly and Traffic Manager references and linked exclusivity rules, LaunchPad route, station path, model wait and recovery readback, geometry, segment layout, and coordinate frame comparisons, mapp View Assembly6D links, AS4 build and complete simulation preflight, offline ARsim RUC builds, compiled RUC and mapp View binding readback, loopback mapp View HTTP and browser smoke tests, direct OPC UA counter reads, and combined HMI checks.
---

# B&R Automation Studio

Use the `project_inspect` operation for a saved `.apj` backed by its Physical and Logical trees. Select the actual configuration and CPU when the project declares multiple choices.

Find `br-studio` on PATH or use the checkout's `.local/venv/Scripts/br-studio.exe` after installation. Confirm the operation is available with `br-studio operations`, then read `br-studio operation-help project_inspect`. Use the [saved-project workflow](references/project-inspection.md) for the request, exit codes, and readback.

Use `build_preflight` when checking an identified AS4 installation against the saved project. Read the [build preflight workflow](references/build-preflight.md) for Runtime catalog selection and diagnostic scope.

Use the [complete simulation preflight](references/simulation-preflight.md) to verify the intended mapp 6D line, installed simulator identity, and the supplied LaunchPad export before the motion run.

For a native build on a trusted saved copy, follow the [native AS4 build workflow](references/native-build.md). It pins the source and toolchain and retains the RUC and builder log.

Use the [RUC review workflow](references/ruc-review.md) to bind the package to a successful job and compare its compiled 6D routes and widget links with the matching saved project.

Use the [LaunchPad route workflow](references/launchpad-route-review.md) to compare a saved unbranched route with the selected manager in source or a retained RUC.

Use the [LaunchPad station workflow](references/launchpad-station-review.md) to follow each leg between stations and read its source station model waits before checking the native recipe.

Use the [LaunchPad recovery workflow](references/launchpad-recovery-review.md) to compare station declarations and the saved and compiled point sequence.

Use the [Traffic Manager rule workflow](references/traffic-rule-review.md) to compare linked exclusivity groups and their typed member, entry, and exit references.

Use the [LaunchPad geometry workflow](references/launchpad-geometry-review.md) to measure manager object spacing and handedness against the same cycle.

Use the [LaunchPad layout workflow](references/launchpad-layout-review.md) for the linked S3 segment grid and master-relative layout.

Use the [LaunchPad coordinate frame workflow](references/launchpad-frame-review.md) to measure object residuals under the grid-compatible axes. Supply a common datum when verified coordinates are available.

Use the [binding review workflow](references/ruc-binding-review.md) to verify the selected one-way widget binding and PLC node exposure in the compiled RUC.

For local runtime HTTP evidence on a reviewed RUC, follow the [ARsim smoke workflow](references/arsim-smoke.md).

Use the [browser smoke workflow](references/arsim-browser-smoke.md) to verify a rendered Assembly6D widget and an advancing PLC-bound counter through a page reopen.

Use the [direct OPC UA smoke workflow](references/arsim-opcua-smoke.md) for an independent read of the selected simulated PLC counter and its reported access levels.

Use the [combined HMI workflow](references/arsim-hmi-smoke.md) to verify the selected binding and compare direct and browser values inside one ARsim session.

Keep requests and results containing real project details in a project-owned folder or the checkout's ignored `.local/`. Use the read-set SHA-256 and selected configuration when reporting findings. Ground any claim about compiling, running ARsim, or operating on hardware in separate evidence.
