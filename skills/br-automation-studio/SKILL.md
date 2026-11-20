---
name: br-automation-studio
description: Inspect saved B&R Automation Studio projects with the standalone br-studio CLI. Use for declared package versions, ACOPOS 6D assembly and Traffic Manager references, mapp View Assembly6D links, AS4 build preflight, offline ARsim RUC builds, compiled RUC readback, and loopback mapp View HTTP smoke tests.
---

# B&R Automation Studio

Use the `project_inspect` operation for a saved `.apj` backed by its Physical and Logical trees. Select the actual configuration and CPU when the project declares multiple choices.

Find `br-studio` on PATH or use the checkout's `.local/venv/Scripts/br-studio.exe` after installation. Confirm the operation is available with `br-studio operations`, then read `br-studio operation-help project_inspect`. Use the [saved-project workflow](references/project-inspection.md) for the request, exit codes, and readback.

Use `build_preflight` when checking an identified AS4 installation against the saved project. Read the [build preflight workflow](references/build-preflight.md) for Runtime catalog selection and diagnostic scope.

For a native build on a trusted saved copy, follow the [native AS4 build workflow](references/native-build.md). It pins the source and toolchain and retains the RUC and builder log.

Use the [RUC review workflow](references/ruc-review.md) to bind the package to a successful job and compare its compiled 6D routes and widget links with the matching saved project.

For local runtime HTTP evidence on a reviewed RUC, follow the [ARsim smoke workflow](references/arsim-smoke.md).

Keep requests and results containing real project details in a project-owned folder or the checkout's ignored `.local/`. Use the read-set SHA-256 and selected configuration when reporting findings. Ground any claim about compiling, running ARsim, or operating on hardware in separate evidence.
