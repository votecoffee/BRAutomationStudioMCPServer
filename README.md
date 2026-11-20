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

Saved-file inspection runs without Automation Studio. Native build workflows will require a compatible B&R installation.

## Usage

### CLI

```powershell
$brStudio = '.\.local\venv\Scripts\br-studio.exe'
& $brStudio operations
& $brStudio operation-help project_inspect
& $brStudio run --request examples/inspect-loop.json
& $brStudio run --request examples/inspect-loop.json --format markdown
```

The example reads a synthetic project. The result lists declared package versions, selected configuration and CPU, layout and assembly references, Traffic Manager endpoints, and Assembly6D widget links. It records SHA-256 hashes for the files read. Use the result as saved-file evidence. Native compilation and ARsim behavior need their own checks.

## Architecture

The application design covers the shared operation catalog, standalone CLI, future chat skill, and the native build boundary. [Toolset architecture](docs/ARCHITECTURE.md)

## Maintainers

Project contact [votecoffee](https://github.com/votecoffee).

## Contributing

Use [GitHub Issues](https://github.com/votecoffee/BRAutomationStudioMCPServer/issues) for questions and reproducible findings. Submit proposed changes as pull requests with a description of the supported versions and the checks performed. Use synthetic or sanitized fixtures that you have permission to share.

## License

[MIT](LICENSE) © 2026 votecoffee.
