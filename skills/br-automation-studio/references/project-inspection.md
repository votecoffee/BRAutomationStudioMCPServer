# Inspect a saved Automation Studio project

## Run the inspection

1. Select the saved Automation Studio `.apj` and its matching project tree. When opening a repository example, use `examples/inspect-loop.json` to verify the installed CLI first.
2. Run `br-studio operation-help project_inspect`. Create a UTF-8 JSON request without a BOM. `workspace` identifies the request in its result. A relative `project` path is resolved from the request file.
3. Run `br-studio run --request <file>` for the structured result or add `--format markdown` for a readable report. If you run both commands, compare the read-set hashes before treating the outputs as the same observation.

Example request for a project with one configuration and CPU.

```json
{
  "schemaVersion": 1,
  "workspace": "assembly-review",
  "operation": "project_inspect",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj"
  }
}
```

The inspector selects a single declared configuration and CPU automatically. Supply `configuration` and `cpu` as needed when there is more than one choice. A partial result whose diagnostics request a selection can help identify the available names.

## Read the result

| Exit | Status | Meaning |
| --- | --- | --- |
| 0 | `complete` | The supported saved-file checks ran without diagnostics. |
| 1 | `issues` | The inspector reported errors such as a missing file, malformed XML, or a missing or mistyped route endpoint. The result retains the files and edges it could read. |
| 2 | `invalid_request` | The CLI could not accept the arguments or the request. Request errors are JSON, including when Markdown output was requested. |
| 3 | `partial` | Warnings delimit the result, such as a required configuration choice or a widget link unresolved within the selected CPU. |

Check `result.selectedConfiguration` and `result.selectedCpu` first. Then read `technologyPackages`, `assemblyLinks`, `trafficManagers`, `widgets` and `diagnostics`. Compare route endpoints within the manager that owns them. A logical widget can belong to another visualization or configuration. Confirm visibility and activation separately before changing its link.

For the selected manager's exclusivity groups, inspect `trafficManagers[].ruleEvidence` or the Markdown rule section. [Traffic rule review](traffic-rule-review.md)

`snapshot.files` lists the relative paths and exact SHA-256 hashes of the files read. The aggregate SHA-256 identifies that read set. Use a stable saved copy if the project is being changed elsewhere. The inspector checks the recorded AS4 XML forms, declared links, and typed endpoints. Geometry, recipe behavior, live bindings, installed package availability, and runtime activation need their own evidence.
