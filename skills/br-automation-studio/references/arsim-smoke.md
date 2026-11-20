# Run a loopback mapp View HTTP smoke test

Use `arsim_smoke` with a trusted offline `PC_any` project, its successful [build receipts and RUC](native-build.md), and an installed PVI 4 `PVITransfer.exe`. The operation creates a private runtime and verifies the HTTP payload it serves using the [reviewed package](ruc-review.md).

## Select the loopback fixture

Use a saved configuration with explicit `127.0.0.1` values for `SimulationIPAddress` and the `IF3` `InternetAddress`. Select a distinct unprivileged mapp View HTTP port and simulation INA port. If OPC UA is enabled, select a distinct server port and provide explicit local HMI connection settings. The operation checks the saved and compiled OPC UA endpoints against the configured port. Give the run a new session directory under an existing parent outside the input trees. PVI staging paths use printable ASCII.

Discover the contract with `br-studio operation-help arsim_smoke` and write a UTF-8 JSON request without a BOM.

```json
{
  "schemaVersion": 1,
  "workspace": "loopback-hmi",
  "operation": "arsim_smoke",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "jobDirectory": "C:/BuildJobs/example-arsim-001",
    "pviTransfer": "C:/Program Files/BRAutomation4/PVI/V4.12/PVI/Tools/PVITransfer/PVITransfer.exe",
    "sessionDirectory": "C:/BuildJobs/example-smoke-001",
    "timeoutSeconds": 30
  }
}
```

Run `br-studio run --request <smoke-request.json>`. Select `--format markdown` on that invocation for a readable report. An additional invocation needs a new session directory. The timeout is the HTTP startup window. PVITransfer materialization has its own 75-second limit.

## Interpret the session

- `CreateARsimStructure` generates a fresh copy with `Start=0`. The session records the PVI version, package hash, input profile, loader hash, and vendor log.
- Before launch, the operation checks the configured ports and the observed engineering-port defaults (`11160` and `11169`). It checks the Windows job's owned TCP listeners and UDP endpoints for loopback throughout the probe. Existing listeners on other ports remain outside the session.
- The HTTP test uses `127.0.0.1` directly. It checks status and SHA-256 against the packaged `index.html`, reviewed generated content, and `Assembly6D.js` where applicable. A successful HTTP status with different response bytes is an issue.
- On completion, read `result.arsimSmoke` and `session.json` for the observed endpoints, asset hashes, and cleanup result. The loader runs in an owned Windows job. Save the receipts and logs before discarding the materialized runtime.

The observed AS4 profile serves a verifiable local HMI payload. Use the [headless browser workflow](arsim-browser-smoke.md) for rendering and counter readback. Direct OPC UA readback, simulator motion, and commissioning each need separate evidence.
