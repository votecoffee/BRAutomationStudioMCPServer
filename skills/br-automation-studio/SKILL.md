---
name: br-automation-studio
description: Inspect saved B&R Automation Studio projects with the standalone br-studio CLI. Use for declared package versions, ACOPOS 6D assembly and Traffic Manager references, and mapp View Assembly6D links.
---

# B&R Automation Studio

Use the `project_inspect` operation for a saved `.apj` backed by its Physical and Logical trees. Select the actual configuration and CPU when the project declares multiple choices.

Find `br-studio` on PATH or use the checkout's `.local/venv/Scripts/br-studio.exe` after installation. Confirm the operation is available with `br-studio operations`, then read `br-studio operation-help project_inspect`. Use the [saved-project workflow](references/project-inspection.md) for the request, exit codes, and readback.

Keep requests and results containing real project details in a project-owned folder or the checkout's ignored `.local/`. Use the read-set SHA-256 and selected configuration when reporting findings. Ground any claim about compiling, running ARsim, or operating on hardware in separate evidence.
