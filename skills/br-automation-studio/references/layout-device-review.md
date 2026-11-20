# Review the Assembly6D file-device declaration

Use `layout_device_review` to check the `6DData` declaration in the selected AS4 CPU's `Hardware.hw`. Include `jobDirectory` to read the device of the same source-matched ARsim build in `FilesToTransfer/Data0/$$sysconf.br`.

```json
{
  "schemaVersion": 1,
  "workspace": "layout-device",
  "operation": "layout_device_review",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "configuration": "ARsim",
    "cpu": "PC"
  }
}
```

Run `br-studio run --request layout-device.json --format markdown`. If the project has one configuration and CPU, the saved review selects them automatically.

## Check the declarations

- The saved reader expects a CPU module with an indexed `FileDeviceN` group and its paired `FileDeviceNameN` and `FileDevicePathN` parameters. It identifies the slot with the exact `6DData` name and reports duplicate or incomplete fields. [B&R file-device configuration](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/60_configure_file_device.html)
- The compiled reader checks the named node in the ARsim system XML device tree after verifying the build receipt and the source read set. A saved target of `USER_PATH` should appear as `/DEVICE=USER_PATH` in the compiled node. The report preserves other raw targets and marks their equivalence unverified.
- A complete read establishes the declaration and the supported compiled path equivalence. Check the target directory and generated SVG files after assembly startup. Use the [browser layout asset check](arsim-browser-smoke.md#check-the-layout-assets) for the requests from the actual widget. [B&R blank layout troubleshooting](https://help.br-automation.com/#/en/4/motion/mapp_6d/leitfaden/getting_started_mapp_6d/faq.html)
