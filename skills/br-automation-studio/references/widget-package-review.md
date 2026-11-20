# Inventory a saved mapp View widget library

Use `widget_package_review` before an AS4 build to compare the immediate entries of a saved widget library directory with its `Package.pkg`. The operation follows the registered `Logical/mappView/Widgets` branch and checks its declared child packages typed as `WidgetLibrary`. It reads project metadata and directory entries offline.

```json
{
  "schemaVersion": 1,
  "workspace": "widget-inventory",
  "operation": "widget_package_review",
  "arguments": { "project": "Project/Example.apj" }
}
```

Run `br-studio run --request <request.json>`. Add `--format markdown` for the readable inventory. Keep the output with the saved project and its build receipt.

- `matched` means the checked immediate files and directories agree with their registrations. The library manifest itself is implicit.
- `differences` reports extra entries and declared members that are absent or have a different type. Undeclared files and directories carry the corresponding AS4 warning identifiers **9232** and **9233**. The native log can differ according to the effective build options and compiler behavior.
- `not_registered` indicates the inspected branch has no registered widget libraries. `unverified` identifies a registration or inventory the saved check could not resolve.

Check the native builder log for generation and copy steps before dispositioning the extra entries. B&R widget templates can include assets used during mapp View compilation even when the project manifest omits their individual names. Check the [compiled RUC](ruc-review.md) for the widget script and generated content that the HMI needs. [B&R AS4 configuration properties](https://help.br-automation.com/#/en/4/projectmanagement/configurationview/projectorganisation_configurationview_objectproperties.html)
