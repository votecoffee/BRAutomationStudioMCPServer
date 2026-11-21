# Review the contents of an AS4 RUC package

Use `ruc_review` after a successful [native build](native-build.md). Supply the saved `.apj` and the retained directory with `job.json`, `plan.json`, and the RUC at its receipt-relative path. The directory can move as a unit. The source project can also move if its staged files and filenames still match the plan.

## Run the review

Discover the contract with `br-studio operation-help ruc_review`. Write a UTF-8 JSON request without a BOM.

```json
{
  "schemaVersion": 1,
  "workspace": "arsim-package-review",
  "operation": "ruc_review",
  "arguments": {
    "project": "C:/Projects/Example/Example.apj",
    "jobDirectory": "C:/BuildJobs/example-arsim-001"
  }
}
```

Run `br-studio run --request <review-request.json>`. Add `--format markdown` to read the result as a report. If you save both formats, confirm that the RUC and plan hashes agree.

## Verify the default page placement of Assembly6D

With br-studio 0.39.0 or later, add `"startPageWidgetId": "Overview_PlanarDisplay"` to the `ruc_review` request arguments. Use the exact `ContentId_WidgetId` of the Assembly6D widget expected on the default page. The review follows the selected CPU's registered mapp View server configuration and visualization to the saved start page, layout, and area with a direct content assignment.

The report at `rucReview.startPageWidget` identifies the saved files and the matching compiled server configuration, visualization, generated client JSON, and content HTML. The review checks the same content URL in the client map, the single DOM element with the selected ID and Assembly6D class, and the saved and rendered assembly and manager links. This workflow covers the direct AS4 v2 start-page assignment. Continue with the [browser and bundle check](arsim-browser-smoke.md) for runtime evidence.

## Evidence in the result

| Check | Evidence |
| --- | --- |
| Receipt and source | The plan hash recomputed from `plan.json` matches `job.json`. The job records a successful AS4 ARsim build. The supplied saved tree matches the manifest of staged inputs. |
| Archive | The current ZIP's size, SHA-256, and member count match the job receipt. The reviewer reads selected members directly, using their ZIP CRCs. |
| ACOPOS 6D | Readable XML in the expected `FilesToTransfer/Data0/*.br` modules preserves module identities and assembly–layout and manager–list references. The comparison includes station and waypoint types and the multiplicity of each directed edge. Each compiled endpoint is resolved within its own manager. |
| Pinned motion payloads | For a job built with `verifyMotionPayloads`, the review checks all six SG4/IA32 binary hashes against the plan and the retained job. [Motion payload pins](native-build.md#verify-the-motion-payloads-in-the-ruc) |
| LaunchPad route | For a [build with LaunchPad evidence](native-build.md#bind-the-reviewed-launchpad-export-to-the-build), the review rehashes the job's retained `launchpad/` files and checks the selected compiled manager against the reviewed cycle. Read `rucReview.launchPadRoute` for the result. |
| LaunchPad segment grid | With `verifyLaunchPadLayout`, the compiled layout must preserve the reviewed layout identity, master grid position and occupied offsets. Read `rucReview.launchPadRoute.layout` for the comparison. [Pinned segment grid](native-build.md#pin-the-imported-segment-grid) |
| Generated motion globals | For a job built with `verifyMotionSymbols`, retain the generated declaration files at their receipt-relative paths. The review checks their names, types, source lines and file hashes against the saved configuration and job. Read `rucReview.motionSymbols` for the result. [Generated motion globals](native-build.md#verify-the-generated-assembly-and-traffic-manager-globals) |
| Traffic rules | The linked exclusivity groups preserve their names and typed member, entry and exit references. [Traffic rule review](traffic-rule-review.md) |
| mapp View | The expected AS4 content's rendered `brease.setOptions` JSON identifies the widget class, `mpLink` and `mpLinkTrafficManager`. The corresponding Assembly6D script path is present in the archive. |

Read `result.rucReview` for the compiled references, graph and rendered links. A changed archive, missing member, wrong typed endpoint, duplicate edge or changed widget link produces an issue. A recognized module with an unreadable payload can produce a partial result. The JSON retains the diagnosed scope and the elements it could read.

The comparison starts from the supported modules and widgets declared in the saved configuration. For projects with multiple visualizations, use the matching saved build scope when interpreting missing content. The review covers the observed AS4 RUC layout and embedded XML and HTML forms. Assembly geometry, recipe behavior, web-server operation and controller behavior need separate evidence. Builder warnings are reported from the original job alongside the package review status. Use [assembly I/O review](assembly-io-review.md) for compiled I/O references and read-axis flags. Use [ARsim smoke](arsim-smoke.md) for HTTP evidence on a local runtime.
