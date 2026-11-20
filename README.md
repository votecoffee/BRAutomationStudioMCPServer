# BRAutomationStudioMCPServer

Standalone engineering tools for B&R Automation Studio projects.

The first workflow inspects saved Automation Studio projects for ACOPOS 6D, Core Motion, Traffic Manager, and PLC-hosted mapp View. The standalone CLI uses a shared operation catalog that a future MCP adapter can call.

## Install

Use Python 3.11 or later. From PowerShell, clone the repository and install the CLI in a local environment.

```powershell
git clone https://github.com/votecoffee/BRAutomationStudioMCPServer.git
Set-Location BRAutomationStudioMCPServer
py -3 -m venv .local\venv
.\.local\venv\Scripts\python.exe -m pip install .
```

Saved-file inspection runs without Automation Studio. Native AS4 builds require a compatible B&R installation on Windows.

## Usage

### CLI

```powershell
$brStudio = '.\.local\venv\Scripts\br-studio.exe'
& $brStudio operations
& $brStudio operation-help project_inspect
& $brStudio operation-help build_preflight
& $brStudio operation-help build_plan
& $brStudio operation-help build_execute
& $brStudio operation-help ruc_review
& $brStudio run --request examples/inspect-loop.json
& $brStudio run --request examples/inspect-loop.json --format markdown
```

The example reads a synthetic project. The result lists declared package versions, selected configuration and CPU, layout and assembly references, Traffic Manager endpoints, and Assembly6D widget links. It records SHA-256 hashes for the files read. Use the result as saved-file evidence. Native compilation and ARsim behavior need their own checks.

For an installed AS4 toolchain, `build_preflight` compares the project with the selected Studio, Runtime, CPU and technology-package catalogs. [Build preflight workflow](skills/br-automation-studio/references/build-preflight.md)

Use `build_plan` and `build_execute` to compile a pinned AS4 PC simulation project and collect the RUC archive and builder log. [Native build workflow](skills/br-automation-studio/references/native-build.md)

After building, use `ruc_review` to compare the retained package's 6D modules, typed routes, and rendered widget links with the saved inputs. [RUC review workflow](skills/br-automation-studio/references/ruc-review.md)

## Chat workflow

The repository includes a skill for selecting saved inputs and interpreting the inspection result. [B&R Automation Studio skill](skills/br-automation-studio/SKILL.md) · [Saved-project workflow](skills/br-automation-studio/references/project-inspection.md)

## Architecture

The application design covers the shared operation catalog, standalone CLI, chat skill, and the native build boundary. [Toolset architecture](docs/ARCHITECTURE.md)

## Maintainers

Project contact [votecoffee](https://github.com/votecoffee).

## Contributing

Use [GitHub Issues](https://github.com/votecoffee/BRAutomationStudioMCPServer/issues) for questions and reproducible findings. Submit proposed changes as pull requests with a description of the supported versions and the checks performed. Use synthetic or sanitized fixtures that you have permission to share.

## License

[MIT](LICENSE) © 2026 votecoffee.
