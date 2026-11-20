# Review a compiled mapp View value binding

Use `ruc_binding_review` on a saved AS4 project and its [retained RUC build](native-build.md). The operation reads the files and archive offline. It verifies the intended PLC source, enabled OPC UA exposure, the selected visualization's active binding, and the rendered widget ID in the package.

## Select the binding

Use a one-way value binding from the default local B&R OPC UA connection to a widget in the selected visualization. The supported format uses the V1 URI `urn:B&R/pv/` and a scalar numeric `::Task:Variable` identifier. Select the visualization ID from the saved `.vis` and the content and widget IDs from the `.content`. Supply the expected PLC identifier from the design or review baseline. This lets the tool catch a saved and compiled pair that both use an unintended variable.

Use `br-studio operation-help ruc_binding_review` for the request schema.

```json
{
  "schemaVersion": 1,
  "workspace": "loopback-hmi",
  "operation": "ruc_binding_review",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "jobDirectory": "C:/BuildJobs/example-arsim-001",
    "visualizationId": "CounterPanel",
    "contentId": "Overview",
    "widgetId": "CountValue",
    "stringIdentifier": "::Pulse:Count"
  }
}
```

Run `br-studio run --request <binding-request.json>`. Add `--format markdown` for the readable report.

## Verify the saved IEC type

With br-studio 0.47.0 or later, set `verifyIecType` to `true` in the request arguments. The check follows the task in the selected `Cpu.sw` through the registered Logical packages to its `IEC.prg` and local `.var` files. Read `bindingReview.saved.iecDeclaration` for the task class, program, IEC type, source line, file hash and expected OPC UA type. The saved declarations join the pinned source read set.

The review supports directly declared, built-in numeric scalars. For example, a `DINT` declaration expects `i=6` (Int32), and `UDINT` expects `i=7` (UInt32). A missing or ambiguous declaration or a different compiled type fails this check. [PLCopen IEC to OPC UA type mapping](https://reference.opcfoundation.org/specs/OPC-30000/9.2)

The same argument works on `arsim_hmi_smoke` and `arsim_status_smoke`. The status workflow verifies the heartbeat and every configured status declaration before launching ARsim. The direct OPC UA checks then compare the observed runtime integer types with the compiled node map.

## Read the result

| Step | Required evidence |
| --- | --- |
| Build and source identity | The successful build joins to the saved source manifest and the RUC size, entry count and SHA-256. Each parsed saved file joins back to a pinned digest. |
| Saved binding | The CPU declares the selected visualization and its referenced binding set. The selected content and widget are declared, the active value binding has one source and one target, and the task scalar is enabled in the OPC UA `.uad`. |
| Compiled binding and widget | The packaged visualization points to its generated binding file. That file contains one active one-way binding from the expected OPC UA identifier to the selected widget's DOM ID. The generated HTML declares the corresponding widget type. |
| Connection and exposure | The packaged `Data0/OpcUaSrv.br` matches the saved default local connection and namespace. The generated `.uar.gz` corresponding to the saved map contains the task scalar with a supported numeric type and compiled `AccessLevel="0x01"`. |

A complete result includes `ruc_binding_readback` and `bindingReview.state` of `matched`. The result shows the source paths and RUC member names. Use the diagnostic path and the last checked phase to locate mismatches. The check covers one selected widget value in this AS4 layout. Aliased servers, array or structure members, unsupported node-map layouts and other binding modes require separate review.

Use [direct OPC UA readback](arsim-opcua-smoke.md) to observe runtime access for the anonymous session and [headless browser smoke](arsim-browser-smoke.md) to check rendering and updating values on the running HMI. The static report does not determine effective role access, whether the target is visible on the start page, or how the browser session behaves.
