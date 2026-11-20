# Read a PLC counter directly from loopback ARsim

Use `arsim_opcua_smoke` when a mapp View display is stale, blank, or disconnected and you need independent evidence from the PLC variable. Run it with a trusted, reviewed AS4 `PC_any` RUC. It checks the packaged HTTP asset bytes and then opens an anonymous OPC UA session against the same job's verified loopback listener.

## Select the node

Use a fixture with a configured local OPC UA server and explicit local mapp View connection. Check the saved OPC UA variable exposure and the mapp View binding. Copy the exact namespace URI and the string identifier from those inputs. On an AS4 project with the V1 process-variable model, the URI can be `urn:B&R/pv/` and the identifier can be `::Pulse:Count`. The namespace index is resolved from the running server.

Select an exposed read-only scalar integer that keeps increasing during the sampling window. The operation does not import edits or issue UA Write requests. Install the optional client dependency in the environment that runs `br-studio`.

```powershell
.\.local\venv\Scripts\python.exe -m pip install ".[opcua]"
```

Use `br-studio operation-help arsim_opcua_smoke` to discover the request contract. The [ARsim HTTP smoke guide](arsim-smoke.md) describes the input and session directory requirements.

```json
{
  "schemaVersion": 1,
  "workspace": "loopback-hmi",
  "operation": "arsim_opcua_smoke",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "jobDirectory": "C:/BuildJobs/example-arsim-001",
    "pviTransfer": "C:/Program Files/BRAutomation4/PVI/V4.12/PVI/Tools/PVITransfer/PVITransfer.exe",
    "sessionDirectory": "C:/BuildJobs/example-opcua-001",
    "namespaceUri": "urn:B&R/pv/",
    "stringIdentifier": "::Pulse:Count",
    "opcUaTimeoutSeconds": 20
  }
}
```

Run `br-studio run --request <opcua-request.json>`. Add `--format markdown` to render the report. Each invocation needs a new session directory. `timeoutSeconds` covers HTTP startup and `opcUaTimeoutSeconds` covers waiting for OPC UA, resolving the node and sampling.

## Read the result

| Observation | What to check |
| --- | --- |
| Failure while opening the session | Check the OPC UA endpoint and anonymous access on the isolated fixture. An access error here occurs before the selected node is read. |
| Missing or ambiguous namespace | Compare the requested URI to the AS4 project's selected OPC UA information model. Use the live URI resolution and retain the assigned index in the receipt. |
| Failure reading access metadata | Check the node identifier and published variable access. Both `AccessLevel` and `UserAccessLevel` must report `0x01` (`CurrentRead` alone). Record this as server-reported access for the node and session. [AccessLevelType](https://reference.opcfoundation.org/specs/OPC-10000-3/8.57) |
| Failure sampling | Check that the selected variable is cycling, has Good quality, and stays one scalar integer type. Repeated values are ignored. The counter must advance twice after the first accepted read. |
| Direct read passes while the browser display stalls | Compare the saved and compiled binding, mapp View client permissions and the selected DOM widget. Match both receipts to the same build and node. [Headless browser smoke](arsim-browser-smoke.md) |

On success, check `result.evidence` for `arsim_loopback_opcua` and retain `session.json`. It includes the resolved node ID, access attributes, observed values with status codes and available source timestamps, client version, and runtime cleanup. The direct client reads one variable and the standard namespace array. Browser and direct reads come from separate operation invocations, so their samples need not be identical. Shuttle motion and hardware acceptance require separate evidence.
