# BRAutomationStudioMCPServer

Standalone engineering tools for B&R Automation Studio projects.

The first development use case is ACOPOS 6D with Core Motion, Traffic Manager, and a PLC-hosted mapp View HMI. The toolset starts with saved-project inspection and build preflight. The planned command-line interface will be the entry point for engineers and reusable chat workflows. A future MCP adapter can call the same operations.

## Install

Clone the repository to read the project documentation.

```powershell
git clone https://github.com/votecoffee/BRAutomationStudioMCPServer.git
Set-Location BRAutomationStudioMCPServer
```

The repository is in its documentation stage. Native build workflows will require a compatible Windows installation of B&R Automation Studio, Automation Runtime, and the selected technology packages.

## Usage

The first planned workflow takes a saved Automation Studio project, reports its configurations and declared technology package versions, and checks the relationships among the ACOPOS 6D assembly, Traffic Manager, and mapp View widget. It records the source and the scope of the checks. Native compilation and simulation each require their own evidence.

## Maintainers

Project contact [votecoffee](https://github.com/votecoffee).

## Contributing

Use [GitHub Issues](https://github.com/votecoffee/BRAutomationStudioMCPServer/issues) for questions and reproducible findings. Submit proposed changes as pull requests with a description of the supported versions and the checks performed. Use synthetic or sanitized fixtures that you have permission to share.

## License

[MIT](LICENSE) © 2026 votecoffee.
