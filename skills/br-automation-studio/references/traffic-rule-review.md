# Review linked Traffic Manager exclusivity groups

Use `project_inspect` to review the rule lists linked by the selected CPU's Traffic Managers. Use `ruc_review` to compare the saved configuration with a retained AS4 ARsim package. The same graph can have different booking behavior if a rule, member, entry, or exit changes. [Saved project inspection](project-inspection.md) · [RUC review](ruc-review.md)

## Read the rules

Open `trafficManagers[].ruleEvidence` in the saved result or `rucReview.trafficManagers[].ruleEvidence` in the compiled result. Each entry identifies the linked list, its source file or archive member, and its supported rules. Markdown shows the same names and typed references.

| Field | AS4 2.2 editor group |
| --- | --- |
| `members` | `TrafficObjects` |
| `entries` | `EntryObjects` |
| `exits` | `ExitObjects` |

The parser reads `TrafficRule` selectors of type `ExclusivityGroup` and resolves each `ExclusivityGroupObjectRef` to a Station or Waypoint in the same manager. The RUC comparison checks the group name, type, and multiplicity of typed references within each role. It allows compiler metadata and reordering within a role. Moving an endpoint between roles changes the result.

A linked, readable list can be empty. An unsupported rule type or shape receives an `unsupported_rule` warning and makes that list `unverified`. An unresolved or mistyped endpoint receives `invalid_rule_endpoint`. An on-disk rule list outside the selected manager's links is outside its rule evidence.

## Check motion behavior separately

The schema comes from the AS4 mapp 6D 2.2.0 catalog under `AS/TechnologyPackages/mapp6D/2.2.0/Editors/` in `TrafficRuleList.xml` and `ExclusivityGroupObjectList.xml`. The installed Help topic `konzept/traffic_manager/exclusivity.html` describes booking at main objects and along routes involving entry and exit objects. Confirm the intended group boundaries and booking and release sequence in the version-matched simulation. Preserve the selected recipe and observed shuttle route when documenting that check.
