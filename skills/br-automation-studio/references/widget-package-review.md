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

## Join the saved inventory to a completed build

With br-studio 0.37.0 or later, add `"jobDirectory": "build-jobs/example"` to the request arguments for the retained job. Keep its `plan.json`, `job.json`, `builder.log` and RUC in the recorded layout. The review checks that the saved project matches the staged input manifest and that the log and RUC match the job's hashes.

Each extra entry then shows matching 9232 or 9233 log lines, logged copies from the staged project into generated mapp View web content, and RUC files at the corresponding path in the widget library's web subtree. The JSON member list records the archive file's hash and its same-path source match when the build manifest contains that source. A null saved-byte match means the source manifest has no file at that path. Empty lists mean the review found no match in the recognized English builder log lines or checked archive subtree. Changed inputs, a changed log or a changed archive make the requested build evidence unverified.

Check the native builder log for generation and copy steps before dispositioning the extra entries. B&R widget templates can include assets used during mapp View compilation even when the project manifest omits their individual names. Check the [compiled RUC](ruc-review.md) for the widget script and generated content that the HMI needs. [B&R AS4 configuration properties](https://help.br-automation.com/#/en/4/projectmanagement/configurationview/projectorganisation_configurationview_objectproperties.html)
