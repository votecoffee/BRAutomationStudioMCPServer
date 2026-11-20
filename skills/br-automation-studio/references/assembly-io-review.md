# Review ACOPOS 6D assembly I/O

Use `assembly_io_review` to check the `IOMapp` Input and Output references and the six `CyclicChannel6D/ReadChannel` axis flags on an AS4 assembly. Select the assembly ID under the intended configuration and CPU. The selection uses the assembly symbol rather than the filename.

```json
{
  "schemaVersion": 1,
  "workspace": "assembly-io",
  "operation": "assembly_io_review",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "configuration": "ARsim",
    "cpu": "PC",
    "assemblyId": "FixtureAssembly"
  }
}
```

Run `br-studio run --request assembly-io.json --format markdown`. Include `jobDirectory` to check a retained AS4 RUC after its build receipt and saved input set are verified.

## Read the reference and flag evidence

- The source reader resolves bare IEC reference names in the registered root `Logical/Global.var`. It recognizes simple declarations in `VAR` or `VAR_GLOBAL` sections, reports their line numbers, and checks the `Mc6DControllerFBIOType` type. Names and type identifiers are case-insensitive. An unregistered file, a qualified name, or a declaration outside the supported shape leaves the reference unverified. [B&R global communication variables](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/31_define_communication_variables.html)
- For a compiled review, the selected reference is resolved against the hash-bound saved declaration. A changed explicit reference or axis flag is an issue. The topic shows separate Input and Output fields. The six supported flags address X, Y, Z, Rx, Ry and Rz. [B&R AS4 assembly configuration](https://help.br-automation.com/#/en/4/motion/mapp_6d/konfiguration/komponenten/assembly.html)
- A flag reports `materialized` when the saved configuration leaves it absent and the compiler supplies a readable Boolean. A saved-only review preserves the omission. `FALSE` is a valid explicit state.
- The review covers the saved IEC declaration and the packaged configuration. Verify the controller channel mapping, communication and live axis values with the target setup.
