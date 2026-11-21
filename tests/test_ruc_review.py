
from gzip import compress
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from br_automation_studio.binding_review import NODESETS, SERVER
from br_automation_studio.cli import markdown
from br_automation_studio.native_build import identity, source_tree
from br_automation_studio.operations import execute
from br_automation_studio.ruc_review import CONTENT, DATA, VIEW, WIDGET_SCRIPT

FIXTURE = Path(__file__).resolve().parent / "fixtures/Loop"
RUC_PATH = "binary/ARsim/PC/RUCPackage/RUCPackage.zip"
MAPPING = f"{DATA}/DemoMappings.br"
ASSEMBLY = f"{DATA}/FixtureAssembly.br"
RULES = f"{DATA}/DemoRules.br"
WIDGET = f"{CONTENT}/DemoOverview.html"
DISPLAY = f"{SERVER}/CounterPanel.vis"
BINDING = f"{SERVER}/2100.binding"
MAP = f"{NODESETS}/Exposure.uar.gz"
CONNECTION = f"{DATA}/OpcUaSrv.br"


class RucReviewTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.project = self.root / "Loop"
        self.job = self.root / "retained"
        self.archive = self.job / RUC_PATH
        shutil.copytree(FIXTURE, self.project)
        self.archive.parent.mkdir(parents=True)
        manifest = {
            "version": 1, "mode": "as4_arsim_ruc", "project": "Loop.apj",
            "configuration": "ARsim", "cpu": "PC", "inputs": source_tree(self.project),
        }
        self.plan = {"sha256": identity(manifest), "manifest": manifest}
        self.write("plan.json", self.plan)
        modules = self.project / "Physical/ARsim/PC/mapp6D"
        self.members = {
            f"{DATA}/{path.stem}.br": b"synthetic BR prefix\0" + path.read_bytes() + b"\0suffix"
            for path in modules.iterdir() if path.suffix != ".pkg"
        }
        self.members[WIDGET] = (
            b'<script>\n brease.setOptions("DemoOverview_LoopOverview", {'
            b'"className":"widgets/mapp6D/Assembly6D",'
            b'"mpLink":"FixtureAssembly","mpLinkTrafficManager":"mapp6D::DemoTraffic"});\n</script>'
        )
        self.members[WIDGET_SCRIPT] = b"synthetic widget script placeholder"

    def write(self, filename, data):
        path = self.job / filename
        path.write_text(json.dumps(data), encoding="utf-8", newline="\n")

    def package(self):
        with ZipFile(self.archive, "w") as package:
            for name, data in self.members.items():
                package.writestr(name, data)
            self.assertIsNone(package.testzip())
        with self.archive.open("rb") as stream:
            sha256 = file_digest(stream, "sha256").hexdigest()
        job = {
            "jobId": "synthetic-build", "planSha256": self.plan["sha256"],
            "status": "succeeded", "timedOut": False, "cleanup": "empty", "nativeExitCode": 1,
            "summary": {"errors": 0, "warnings": 1},
            "package": {"path": RUC_PATH, "sha256": sha256,
                        "bytes": self.archive.stat().st_size, "verifiedZip": True, "entries": len(self.members)},
        }
        self.write("job.json", job)
        return job

    def review(self, **options):
        return execute({"schemaVersion": 1, "workspace": "readback", "operation": "ruc_review",
                        "arguments": {"project": "Loop/Loop.apj", "jobDirectory": "retained", **options}}, self.root)

    def add_start_page(self):
        cpu = self.project / "Physical/ARsim/PC/Cpu.pkg"
        cpu.write_bytes(cpu.read_bytes().replace(
            b"</Objects>", b'<Object Type="File">Server.mappviewcfg</Object>'
            b'<Object Type="File">Panel.vis</Object></Objects>'))
        logical = self.project / "Logical/mappView/Package.pkg"
        logical.write_bytes(logical.read_bytes().replace(
            b"</Objects>", b'<Object Type="File">Overview.page</Object>'
            b'<Object Type="File">Panel.layout</Object></Objects>'))
        configuration = (b'<Configuration><Element Type="MAPPVIEWCFG"><Group ID="Client">'
                         b'<Property ID="defaultVisu" Value="CounterPanel"/></Group></Element></Configuration>')
        visualization = (b'<v:Visualization xmlns:v="http://www.br-automation.com/iat2015/visualizationDefinition/v2"'
                         b' id="CounterPanel"><StartPage pageRefId="Overview"/><Pages><Page refId="Overview"/></Pages>'
                         b'<Contents><Content refId="DemoOverview"/></Contents></v:Visualization>')
        (cpu.parent / "Server.mappviewcfg").write_bytes(configuration)
        (cpu.parent / "Panel.vis").write_bytes(visualization)
        page = logical.parent / "Overview.page"
        page.write_bytes(b'<p:Page xmlns:p="http://www.br-automation.com/iat2015/pageDefinition/v2"'
                         b' id="Overview" layoutRefId="Panel"><Assignments>'
                         b'<Assignment type="Content" areaRefId="Main" baseContentRefId="DemoOverview"/>'
                         b'</Assignments></p:Page>')
        (logical.parent / "Panel.layout").write_bytes(
            b'<l:Layout xmlns:l="http://www.br-automation.com/iat2015/layoutDefinition/v2" id="Panel">'
            b'<Areas><Area id="Main"/><Area id="Side"/></Areas></l:Layout>')
        self.plan["manifest"]["inputs"] = source_tree(self.project)
        self.plan["sha256"] = identity(self.plan["manifest"])
        self.write("plan.json", self.plan)
        self.members[f"{SERVER}/Server.mappviewcfg"] = configuration
        self.members[DISPLAY] = visualization
        self.members[WIDGET] += (
            b'<div id="DemoOverview_LoopOverview" data-brease-widget="widgets/mapp6D/Assembly6D">CONTENT</div>')
        client = {"startPage": "Overview",
                  "pages": {"Overview": {"layout": "Panel", "assignments": [
                      {"areaId": "Main", "type": "Content", "contentId": "DemoOverview"}]}},
                  "layouts": {"Panel": {"areas": {"Main": {}, "Side": {}}}},
                  "contents": {"DemoOverview": {"path": "/contents/mappView/DemoOverview.html"}}}
        self.members[f"{VIEW}/CounterPanel.json"] = json.dumps(client).encode("utf-8")
        return client

    def test_start_page_widget_matches_the_saved_and_generated_startup_path(self):
        self.add_start_page()
        self.package()
        response = self.review(startPageWidgetId="DemoOverview_LoopOverview")
        self.assertEqual(response["status"], "complete", response["result"]["diagnostics"])
        found = response["result"]["rucReview"]["startPageWidget"]
        self.assertEqual(found["state"], "matched")
        self.assertEqual(found["saved"]["areaId"], "Main")
        self.assertEqual(found["compiled"]["clientMap"], f"{VIEW}/CounterPanel.json")
        self.assertEqual(found["compiled"]["assembly"], "FixtureAssembly")
        self.assertIn("CounterPanel → Overview → Panel → Main → DemoOverview", markdown(response))

    def test_widget_options_cannot_substitute_for_the_generated_placement_or_dom(self):
        client = self.add_start_page()
        path = f"{VIEW}/CounterPanel.json"
        original = self.members[path]
        placement = client["pages"]["Overview"]["assignments"][0]
        placement["areaId"] = "Side"
        self.members[path] = json.dumps(client).encode("utf-8")
        self.package()
        response = self.review(startPageWidgetId="DemoOverview_LoopOverview")
        self.assertEqual(self.codes(response), ["start_page_widget_mismatch"])
        self.assertIn("assignment differs", response["result"]["diagnostics"][0]["message"])

        self.members[path] = original
        self.members[WIDGET] = self.members[WIDGET].split(b"<div", 1)[0]
        self.package()
        response = self.review(startPageWidgetId="DemoOverview_LoopOverview")
        self.assertEqual(self.codes(response), ["start_page_widget_mismatch"])
        self.assertIn("DOM element", response["result"]["diagnostics"][0]["message"])
        self.assertEqual(self.review()["status"], "complete")


    def codes(self, response):
        return [item["code"] for item in response["result"]["diagnostics"]]

    def test_readback_binds_saved_inputs_and_reviews_all_expected_elements(self):
        package = self.package()["package"]
        response = self.review()
        review = response["result"]["rucReview"]
        self.assertEqual(response["status"], "complete", response)
        self.assertEqual(review["archive"]["sha256"], package["sha256"])
        self.assertTrue(review["sourceMatched"])
        self.assertEqual(len(review["modules"]), 6)
        self.assertEqual(len(review["trafficManagers"][0]["edges"]), 3)
        self.assertEqual(review["widgets"][0]["manager"], "mapp6D::DemoTraffic")
        self.assertIn("Inspect (Station) → Load (Station)", markdown(response))
        self.assertIn("Builder errors 0 · Builder warnings 1", markdown(response))

    def test_changed_source_cannot_be_used_as_build_baseline(self):
        self.package()
        (self.project / "Logical/Extra.st").write_text("x := 1;", encoding="utf-8")
        response = self.review()
        self.assertEqual(self.codes(response), ["ruc_source_changed"])
        self.assertIsNone(response["result"]["rucReview"]["archive"])

    def test_generated_svg_comparison_checks_saved_compiled_and_rendered_positions(self):
        modules = self.project / "Physical/ARsim/PC/mapp6D"
        layout = modules / "FixtureLayout.layout6d"
        objects = modules / "DemoObjects.trafficobjectlist"
        layout.write_text("""<?xml version="1.0" encoding="utf-8"?>
<Configuration><Element ID="FixtureLayout" Type="layout6d">
<Property ID="GridSize" Value="0"/>
<Group ID="UserOrigin"><Property ID="OffsetX" Value="0"/><Property ID="OffsetY" Value="0"/></Group>
<Group ID="Segments"><Group ID="Segment[1]"><Property ID="TypeGrid240" Value="0"/>
<Property ID="X" Value="0"/><Property ID="Y" Value="0"/><Property ID="IsMaster" Value="true"/>
</Group></Group></Element></Configuration>""", encoding="utf-8")
        source = ET.fromstring(objects.read_bytes())
        for index, row in enumerate(source.findall("./Element/Selector"), start=1):
            position = ET.SubElement(row, "Selector", ID="Position", Value="Absolute")
            ET.SubElement(position, "Property", ID="X", Value=f"0.0{index}")
            ET.SubElement(position, "Property", ID="Y", Value="0.02")
        objects.write_bytes(ET.tostring(source, encoding="utf-8", xml_declaration=True))
        for path in (layout, objects):
            self.members[f"{DATA}/{path.stem}.br"] = b"prefix\0" + path.read_bytes() + b"\0suffix"
        manifest = dict(self.plan["manifest"], inputs=source_tree(self.project))
        self.plan = {"manifest": manifest, "sha256": identity(manifest)}
        self.write("plan.json", self.plan)
        self.package()
        assembly = self.root / "assembly.svg"
        traffic = self.root / "traffic.svg"
        assembly.write_text("""<svg xmlns="http://www.w3.org/2000/svg" id="FixtureAssembly-svg">
<g class="assembly" id="FixtureAssembly"><g class="layout" id="FixtureLayout" grid-size="240">
<g class="segment" id="segment-1" segment-id="1" master="true" position-x="0" position-y="0"
position-x-mm="0" position-y-mm="0" transform="translate(0 -240)"><g>
<rect width="240" height="240"/></g></g></g></g></svg>""", encoding="utf-8")
        overlay = """<svg xmlns="http://www.w3.org/2000/svg" id="DemoTraffic-svg">
<g class="traffic-manager" id="DemoTraffic">
<g class="station" id="station_Load" transform="translate(-5 -35)"><rect width="30" height="30"/></g>
<g class="waypoint" id="waypoint_Transit" transform="translate(20 -20)"><ellipse rx="12" ry="12"/></g>
<g class="station" id="station_Inspect" transform="translate(15 -35)"><rect width="30" height="30"/></g>
<g class="link gray-one-way" id="mapping_Load_to_Transit"><line/></g>
<g class="link gray-one-way" id="mapping_Transit_to_Inspect"><line/></g>
<g class="link gray-one-way" id="mapping_Inspect_to_Load"><line/></g>
</g></svg>"""
        request = {"schemaVersion": 1, "workspace": "readback", "operation": "layout_svg_review",
                   "arguments": {"project": "Loop/Loop.apj", "jobDirectory": "retained", "managerId": "DemoTraffic",
                                 "assemblySvg": assembly.name, "trafficSvg": traffic.name}}
        traffic.write_text(overlay, encoding="utf-8")
        matched = execute(request, self.root)
        self.assertEqual(matched["status"], "complete", matched)
        result = matched["result"]["layoutSvgReview"]
        self.assertEqual(len(result["edges"]), 3)
        self.assertEqual(result["objects"][0]["svgMm"], (30, 20))
        self.assertIn("Segments matched 1", markdown(matched))

        failures = (
            ("translate(20 -20)", "translate(21 -20)", "layout_svg_position_mismatch"),
            ("mapping_Load_to_Transit", "mapping_Transit_to_Load", "layout_svg_mismatch"),
        )
        for original, changed, code in failures:
            with self.subTest(code=code):
                traffic.write_text(overlay.replace(original, changed), encoding="utf-8")
                response = execute(request, self.root)
                self.assertEqual(response["status"], "issues")
                self.assertEqual(self.codes(response), [code])

        traffic.write_text(overlay, encoding="utf-8")
        member = f"{DATA}/DemoObjects.br"
        self.members[member] = self.members[member].replace(b'Value="0.02"', b'Value="0.04"', 1)
        self.package()
        changed = execute(request, self.root)
        self.assertEqual(changed["status"], "issues")
        self.assertIn("Saved and compiled traffic object positions differ", markdown(changed))

    def test_other_zip_bytes_fail_hash_binding(self):
        self.package()
        with self.archive.open("ab") as stream:
            stream.write(b"unrecorded")
        response = self.review()
        self.assertEqual(self.codes(response), ["ruc_hash_mismatch"])

    def test_plan_manifest_drift_is_detected_before_source_review(self):
        self.package()
        self.plan["manifest"]["cpu"] = "OtherPC"
        self.write("plan.json", self.plan)
        response = self.review()
        self.assertEqual(self.codes(response), ["ruc_review_input"])
        self.assertFalse(response["result"]["rucReview"]["sourceMatched"])

    def test_compiled_route_must_preserve_the_planned_grid_and_positions(self):
        saved = self.project / "Physical/ARsim/PC/mapp6D/FixtureLayout.layout6d"
        segments = b"""<Group ID="Segments">
          <Group ID="Segment[1]"><Property ID="TypeGrid240" Value="0"/>
            <Property ID="X" Value="0"/><Property ID="Y" Value="0"/><Property ID="IsMaster" Value="true"/></Group>
          <Group ID="Segment[2]"><Property ID="TypeGrid240" Value="0"/>
            <Property ID="X" Value="1"/><Property ID="Y" Value="0"/><Property ID="IsMaster" Value="false"/></Group>
        </Group>"""
        layout = saved.read_bytes().replace(b"</Element>", segments + b"</Element>")
        saved.write_bytes(layout)
        member = f"{DATA}/FixtureLayout.br"
        self.members[member] = b"synthetic BR prefix\0" + layout + b"\0suffix"
        objects = saved.with_name("DemoObjects.trafficobjectlist")
        document = ET.fromstring(objects.read_bytes())
        positions = {}
        for index, row in enumerate(document.findall("./Element/Selector"), start=1):
            position = ET.SubElement(row, "Selector", ID="Position", Value="Absolute")
            ET.SubElement(position, "Property", ID="X", Value=f"0.{index}")
            ET.SubElement(position, "Property", ID="Y", Value="0.120")
            name = row.find("./Property[@ID='Name']").get("Value")
            positions[name] = {"type": row.get("Value"), "xyMetres": [f"0.{index}", "0.12"]}
        objects.write_bytes(ET.tostring(document, encoding="utf-8", xml_declaration=True))
        object_member = f"{DATA}/DemoObjects.br"
        self.members[object_member] = b"synthetic BR prefix\0" + objects.read_bytes() + b"\0suffix"
        evidence = {}
        for name, payload in (("project", "source.lpj"), ("readback", "readback.json"), ("export", "export.x6d")):
            path = self.job / "launchpad" / payload
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(payload.encode())
            evidence[name] = {"path": path.relative_to(self.job).as_posix(), "sha256": sha256(path.read_bytes()).hexdigest()}
        manifest = self.plan["manifest"]
        manifest["inputs"] = source_tree(self.project)
        manifest["launchPadRoute"] = {
            "manager": "DemoTraffic", "files": evidence,
            "objects": [{"name": name, "type": kind, "geometryId": name, "importedName": name}
                        for name, kind in (("Load", "Station"), ("Transit", "Waypoint"), ("Inspect", "Station"))],
            "layout": {"assembly": "FixtureAssembly", "layoutId": "FixtureLayout", "gridMm": 240,
                       "segmentType": "S3-AS", "grid": {"count": 2, "masterPositionMm": [0, 0],
                                                       "offsetsFromMasterMm": [[0, 0], [240, 0]]}},
            "positions": positions,
        }
        self.plan["sha256"] = identity(manifest)
        self.write("plan.json", self.plan)
        self.write("job.json", self.package() | {"launchPadRoute": {"manager": "DemoTraffic", "files": evidence}})
        verified = self.review()
        self.assertEqual(verified["status"], "complete", verified["result"]["diagnostics"])
        route = verified["result"]["rucReview"]["launchPadRoute"]
        self.assertEqual(route["layout"]["state"], "matched")
        self.assertEqual(route["positions"]["state"], "matched")
        self.assertIn("Compiled LaunchPad route positions matched", markdown(verified))

        compiled_objects = self.members[object_member]
        self.members[object_member] = compiled_objects.replace(b'ID="X" Value="0.1"', b'ID="X" Value="0.11"', 1)
        self.write("job.json", self.package() | {"launchPadRoute": {"manager": "DemoTraffic", "files": evidence}})
        moved = self.review()
        self.assertEqual(self.codes(moved), ["ruc_launchpad_positions_mismatch"])
        route = moved["result"]["rucReview"]["launchPadRoute"]
        self.assertEqual((route["state"], route["layout"]["state"]), ("matched", "matched"))
        self.assertEqual(route["positions"]["movedObjects"], ["Load"])
        self.members[object_member] = compiled_objects

        self.members[member] = self.members[member].replace(b'ID="X" Value="1"', b'ID="X" Value="2"')
        self.write("job.json", self.package() | {"launchPadRoute": {"manager": "DemoTraffic", "files": evidence}})
        rejected = self.review()
        self.assertEqual(self.codes(rejected), ["ruc_launchpad_layout_mismatch"])
        route = rejected["result"]["rucReview"]["launchPadRoute"]
        self.assertEqual(route["state"], "matched")
        self.assertEqual(route["layout"]["compiledGrid"]["offsetsFromMasterMm"], [[0, 0], [480, 0]])

    def test_broken_compiled_return_edge_survives_zip_checks_but_fails_review(self):
        self.members[MAPPING] = self.members[MAPPING].replace(
            b'ID="DestinationRef" Value="Load"', b'ID="DestinationRef" Value="Missing"')
        self.package()
        response = self.review()
        self.assertEqual(response["status"], "issues")
        self.assertIn("invalid_mapping_endpoint", self.codes(response))
        self.assertIn("ruc_content_mismatch", self.codes(response))

    def test_duplicate_compiled_edges_are_counted(self):
        data = self.members[MAPPING]
        begin = data.index(b'<Group ID="TrafficMapping[3]">')
        end = data.index(b"</Element>", begin)
        self.members[MAPPING] = data[:end] + data[begin:end].replace(b"TrafficMapping[3]", b"TrafficMapping[4]") + data[end:]
        self.package()
        response = self.review()
        self.assertEqual(response["status"], "issues")
        self.assertEqual(self.codes(response), ["ruc_content_mismatch"])

    def configure_exclusivity(self):
        path = self.project / "Physical/ARsim/PC/mapp6D/DemoRules.trafficrulelist"
        value = b"""<?xml version="1.0" encoding="utf-8"?>
<Configuration><Element Type="trafficrulelist" ID="DemoRules">
  <Selector ID="TrafficRule[1]" Value="ExclusivityGroup">
    <Property ID="Type" Value="ExclusivityGroup"/>
    <Property ID="Name" Value="InspectionZone"/>
    <Group ID="TrafficObjects">
      <Selector ID="TrafficObject[1]" Value="Station"><Property ID="ExclusivityGroupObjectRef" Value="Inspect"/></Selector>
      <Selector ID="TrafficObject[2]" Value="Waypoint"><Property ID="ExclusivityGroupObjectRef" Value="Transit"/></Selector>
    </Group>
    <Group ID="EntryObjects"><Selector ID="TrafficObject[1]" Value="Station">
      <Property ID="ExclusivityGroupObjectRef" Value="Load"/>
    </Selector></Group>
  </Selector>
</Element></Configuration>"""
        path.write_bytes(value)
        manifest = self.plan["manifest"]
        manifest["inputs"] = source_tree(self.project)
        self.plan["sha256"] = identity(manifest)
        self.write("plan.json", self.plan)
        self.members[RULES] = b"synthetic BR prefix\0" + value + b"\0suffix"
        return self.members[RULES]

    def test_compiled_exclusivity_preserves_typed_roles(self):
        value = self.configure_exclusivity()
        # List order and compiler-generated type metadata are immaterial within a role.
        xml = value.split(b"\0")[1]
        document = ET.fromstring(xml)
        members = document.find(".//Group[@ID='TrafficObjects']")
        members[:] = list(reversed(members))
        reference = document.find(".//Property[@Value='Load']")
        reference.set("DataType", "STRING")
        self.members[RULES] = b"synthetic BR prefix\0" + ET.tostring(document, encoding="utf-8", xml_declaration=True)
        self.package()
        response = self.review()
        self.assertEqual(response["status"], "complete", response["result"]["diagnostics"])
        manager, = response["result"]["rucReview"]["trafficManagers"]
        rule_list, = manager["ruleEvidence"]
        rule, = rule_list["rules"]
        self.assertEqual(rule_list["state"], "read")
        self.assertEqual(rule["members"], [{"name": "Transit", "type": "Waypoint"},
                                           {"name": "Inspect", "type": "Station"}])
        self.assertEqual(rule["entries"], [{"name": "Load", "type": "Station"}])
        self.assertEqual(rule["exits"], [])
        self.assertIn("entries Load (Station)", markdown(response))

    def test_compiled_role_change_fails_with_valid_endpoints_and_same_route(self):
        self.members[RULES] = self.configure_exclusivity().replace(b'ID="EntryObjects"', b'ID="ExitObjects"')
        self.package()
        response = self.review()
        self.assertEqual(response["status"], "issues")
        self.assertEqual(self.codes(response), ["ruc_content_mismatch"])
        self.assertIn("DemoRules contents", response["result"]["diagnostics"][0]["message"])

    def test_compiled_rule_rejects_wrong_type_in_selected_manager(self):
        self.members[RULES] = self.configure_exclusivity().replace(b'Value="Inspect"', b'Value="Transit"')
        self.package()
        response = self.review()
        self.assertIn("invalid_rule_endpoint", self.codes(response))
        self.assertIn("expects Station 'Transit', found 'Waypoint'", response["result"]["diagnostics"][0]["message"])

    def test_unknown_rule_format_is_unverified(self):
        self.members[RULES] = self.configure_exclusivity().replace(b'Value="ExclusivityGroup"', b'Value="OtherRule"')
        self.package()
        response = self.review()
        self.assertEqual(response["status"], "partial")
        self.assertEqual(self.codes(response), ["unsupported_rule"])
        rule_list, = response["result"]["rucReview"]["trafficManagers"][0]["ruleEvidence"]
        self.assertEqual(rule_list["state"], "unverified")

    def supply_motion_values(self, *, saved, compiled):
        path = self.project / "Physical/ARsim/PC/mapp6D/FixtureAssembly.assembly6d"
        original = path.read_bytes()

        def insert(values):
            if values is None:
                return original
            speed, acceleration = values
            limits = f"""<Group ID="Shuttles"><Group ID="Limits"><Group ID="InPlaneLimits">
                <Property ID="InPlaneVelLim" Value="{speed}"/>
                <Property ID="InPlaneAccLim" Value="{acceleration}"/>
              </Group></Group></Group>"""
            return original.replace(b"</Element>", limits.encode() + b"</Element>")

        path.write_bytes(insert(saved))
        manifest = self.plan["manifest"]
        manifest["inputs"] = source_tree(self.project)
        self.plan["sha256"] = identity(manifest)
        self.write("plan.json", self.plan)
        self.members[ASSEMBLY] = b"synthetic BR prefix\0" + insert(compiled)

    def motion_review(self, *, missing_exit=False):
        lpj = self.root / "Route.lpj"
        lpj.write_bytes(b"synthetic saved route")
        receipt = self.root / "Route.json"
        routes = [
            ("rs", "s", "Load", "workstation", ["rg"]),
            ("rg", "g", "Transit", "gate", ["ri"]),
            ("ri", "i", "Inspect", "workstation", ["end"]),
            ("end", "s", "recipe_loop_merge_node", "workstation", []),
        ]
        nodes = [dict(recipe_id=key, id=obj, name=name, type_name=kind, targets=targets,
                      exit_velocity=-1 if kind == "gate" else 2, exit_acceleration=10, exit_payload_weight=0)
                 for key, obj, name, kind, targets in routes]
        if missing_exit:
            nodes[0]["exit_velocity"] = True
        decoded = {"launchpad_version": "1.4", "recipe_nodes": nodes,
                   "workstations": [{"id": "s", "name": "Load"}, {"id": "i", "name": "Inspect"}],
                   "gates": [{"id": "g", "name": "Transit"}]}
        with lpj.open("rb") as stream:
            source = {"fileName": lpj.name, "sha256": file_digest(stream, "sha256").hexdigest()}
        receipt.write_text(json.dumps({"schemaVersion": 1, "source": source, "decoded": decoded,
                                       "codec": {"name": "zipson", "version": "0.2.12"}}), encoding="utf-8")
        self.package()
        params = {"project": "Loop/Loop.apj", "jobDirectory": "retained", "managerId": "DemoTraffic",
                  "launchPadProject": "Route.lpj", "launchPadReadback": "Route.json"}
        return execute({"schemaVersion": 1, "workspace": "motion", "operation": "launchpad_motion_review",
                        "arguments": params}, self.root)

    def test_motion_review_reads_materialized_limits_and_raw_exit_values(self):
        self.supply_motion_values(saved=None, compiled=("1.0", "10.0"))
        response = self.motion_review()
        result = response["result"]
        self.assertEqual(response["status"], "complete", result["diagnostics"])
        self.assertEqual(result["evidence"], "launchpad_motion_compiled")
        motion = result["motionReview"]
        velocity = motion["limits"]["InPlaneVelLim"]
        self.assertEqual(velocity, {"unit": "m/s", "saved": {"state": "absent", "raw": None},
                                    "compiled": {"state": "declared", "raw": "1.0"}, "comparison": "materialized"})
        self.assertEqual([row["exit_velocity"] for row in motion["launchPadExitParameters"]], [2, -1, 2])
        self.assertIn("materialized", markdown(response))

    def test_changed_explicit_motion_limit_fails_without_changing_route_evidence(self):
        self.supply_motion_values(saved=("0.75", "8.0"), compiled=("0.5", "8.00"))
        response = self.motion_review()
        result = response["result"]
        self.assertEqual(response["status"], "issues", result["diagnostics"])
        self.assertEqual(result["routeReview"]["state"], "matched")
        self.assertEqual(self.codes(response), ["ruc_motion_limit_mismatch"])
        limits = result["motionReview"]["limits"]
        self.assertEqual(limits["InPlaneVelLim"]["comparison"], "mismatch")
        self.assertEqual(limits["InPlaneAccLim"]["comparison"], "matched")

    def test_invalid_model_exit_is_partial_preserving_compiled_limits(self):
        self.supply_motion_values(saved=None, compiled=("1.0", "10.0"))
        response = self.motion_review(missing_exit=True)
        result = response["result"]
        self.assertEqual(response["status"], "partial", result["diagnostics"])
        self.assertEqual(result["routeReview"]["state"], "matched")
        self.assertEqual(self.codes(response), ["launchpad_motion_unverified"])
        self.assertIsNone(result["motionReview"]["launchPadExitParameters"][0]["exit_velocity"])
        self.assertEqual(result["motionReview"]["limits"]["InPlaneVelLim"]["comparison"], "materialized")

    def supply_assembly_io(self, *, output_type="Mc6DControllerFBIOType", registered=True, changed_axis=False):
        logical = self.project / "Logical"
        package = logical / "Package.pkg"
        if registered:
            xml = package.read_bytes()
            package.write_bytes(xml.replace(b"</Objects>", b'<Object Type="File">Global.var</Object></Objects>'))
        global_path = logical / "Global.var"
        global_path.write_text(f"""(* comment with a fake gInput : BOOL; *)
VAR
    gInput : Mc6DControllerFBIOType; // legitimate declaration
    gOutput : {output_type};
END_VAR
""", encoding="utf-8-sig")
        path = self.project / "Physical/ARsim/PC/mapp6D/FixtureAssembly.assembly6d"
        original = path.read_bytes()
        mapping = b'<Group ID="IOMapp"><Property ID="Input" Value="gInput"/><Property ID="Output" Value="gOutput"/></Group>'
        channel = b'<Group ID="CyclicChannel6D"><Group ID="ReadChannel">%s</Group></Group>'
        flag = b'<Property ID="%s" Value="%s"/>'
        saved_axis = flag % (b"Ryaxis", b"false")
        compiled_axes = b"".join(flag % (axis, b"TRUE" if axis != b"Ryaxis" or changed_axis else b"FALSE")
                                 for axis in (b"Xaxis", b"Yaxis", b"Zaxis", b"Rxaxis", b"Ryaxis", b"Rzaxis"))
        path.write_bytes(original.replace(b"</Element>", mapping + channel % saved_axis + b"</Element>"))
        self.members[ASSEMBLY] = b"synthetic BR prefix\0" + original.replace(
            b"</Element>", mapping.replace(b"gOutput", b"goutput") + channel % compiled_axes + b"</Element>")
        manifest = self.plan["manifest"]
        manifest["inputs"] = source_tree(self.project)
        self.plan["sha256"] = identity(manifest)
        self.write("plan.json", self.plan)

    def assembly_io(self):
        self.package()
        return execute({"schemaVersion": 1, "workspace": "assembly-io", "operation": "assembly_io_review",
                        "arguments": {"project": "Loop/Loop.apj", "jobDirectory": "retained",
                                      "assemblyId": "FixtureAssembly"}}, self.root)

    def test_assembly_io_matches_root_declarations_and_materialized_axes(self):
        self.supply_assembly_io()
        response = self.assembly_io()
        result = response["result"]
        self.assertEqual(response["status"], "complete", result["diagnostics"])
        self.assertEqual(result["evidence"], "assembly_io_compiled")
        review = result["ioReview"]
        rows = [(row["comparison"], row["compiled"]["value"]) for row in review["readAxes"].values()]
        self.assertEqual(rows.count(("matched", False)), 1)
        self.assertEqual(rows.count(("materialized", True)), 5)
        self.assertEqual(review["references"]["Output"]["comparison"], "matched")
        self.assertEqual(review["references"]["Input"]["declaration"]["line"], 3)
        self.assertEqual(review["references"]["Output"]["declaration"]["state"], "matched")
        self.assertIn("Logical/Global.var line 4", markdown(response))
        self.assertIn("Logical/Global.var", {row["path"] for row in result["snapshot"]["files"]})

    def test_assembly_io_catches_changed_axis_and_incorrect_global_type(self):
        self.supply_assembly_io(output_type="UDINT", changed_axis=True)
        response = self.assembly_io()
        self.assertEqual(response["status"], "issues")
        self.assertEqual(set(self.codes(response)), {"assembly_io_wrong_type", "ruc_io_mismatch"})
        review = response["result"]["ioReview"]
        self.assertEqual(review["readAxes"]["Ryaxis"]["saved"]["value"], False)
        self.assertEqual(review["readAxes"]["Ryaxis"]["compiled"]["value"], True)
        self.assertEqual(review["references"]["Output"]["declaration"]["type"], "UDINT")

    def test_assembly_io_does_not_resolve_unregistered_global_file(self):
        self.supply_assembly_io(registered=False)
        response = self.assembly_io()
        self.assertEqual(response["status"], "partial")
        self.assertEqual(set(self.codes(response)), {"assembly_io_declaration_unverified"})
        self.assertNotIn("Logical/Global.var", {row["path"] for row in response["result"]["snapshot"]["files"]})

    def review_layout_device(self, saved, compiled):
        hardware = self.project / "Physical/ARsim/Hardware.hw"
        params = '' if saved is None else f"""
      <Group ID="FileDevice1"/>
      <Parameter ID="FileDeviceName1" Value="6DData"/>
      <Parameter ID="FileDevicePath1" Value="{saved}"/>"""
        hardware.write_text(f'<Hardware><Module Name="PC">{params}</Module></Hardware>', encoding="utf-8")
        self.plan["manifest"]["inputs"] = source_tree(self.project)
        self.plan["sha256"] = identity(self.plan["manifest"])
        self.write("plan.json", self.plan)
        device = '' if compiled is None else f"""
      <n ID="6DData"><d ID="DeviceParam">{compiled}</d><d ID="DDriverName">DdNtFiLowio</d></n>"""
        tree = f'<?xml version="1.0"?><tree><n ID="DEVICES"><n ID="USER"/>{device}</n></tree>'
        self.members[f"{DATA}/$$sysconf.br"] = b"synthetic BR preface\0" + tree.encode() + b"\0suffix"
        self.package()
        return execute({"schemaVersion": 1, "workspace": "device", "operation": "layout_device_review",
                        "arguments": {"project": "Loop/Loop.apj", "jobDirectory": "retained"}}, self.root)

    def test_layout_device_distinguishes_missing_saved_device_from_default_user_device(self):
        response = self.review_layout_device(None, None)
        result = response["result"]
        self.assertEqual(response["status"], "issues")
        self.assertEqual(self.codes(response), ["layout_device_missing"])
        review = result["layoutDeviceReview"]
        self.assertEqual(review["saved"]["state"], "absent")
        self.assertEqual(review["compiled"]["state"], "absent")
        self.assertIn("Physical/ARsim/Hardware.hw", {row["path"] for row in result["snapshot"]["files"]})

    def test_layout_device_binds_matching_user_partition_target(self):
        response = self.review_layout_device("USER_PATH", "/DEVICE=USER_PATH")
        self.assertEqual(response["status"], "complete", response["result"]["diagnostics"])
        review = response["result"]["layoutDeviceReview"]
        self.assertEqual(review["pathComparison"], "matched")
        self.assertEqual(review["compiled"]["driver"], "DdNtFiLowio")
        self.assertIn("/DEVICE=USER_PATH", markdown(response))

    def test_layout_device_rejects_missing_or_changed_compiled_declaration(self):
        for value, reason in ((None, "layout_device_not_compiled"),
                              ("/DEVICE=Other", "layout_device_path_mismatch")):
            with self.subTest(value=value):
                response = self.review_layout_device("USER_PATH", value)
                self.assertEqual(response["status"], "issues")
                self.assertEqual(self.codes(response), [reason])

    def test_layout_device_leaves_unsupported_saved_path_equivalence_unverified(self):
        response = self.review_layout_device("C:/Temp", "/DEVICE=C:/Temp")
        self.assertEqual(response["status"], "partial")
        self.assertEqual(self.codes(response), ["layout_device_path_unverified"])
        self.assertEqual(response["result"]["layoutDeviceReview"]["pathComparison"], "unverified")

    def test_changed_widget_link_fails_after_a_valid_archive(self):
        self.members[WIDGET] = self.members[WIDGET].replace(b"mapp6D::DemoTraffic", b"mapp6D::Other")
        self.package()
        self.assertEqual(self.codes(self.review()), ["ruc_widget_mismatch"])

    def test_unreadable_motion_payload_is_partial_without_invented_routes(self):
        self.members[MAPPING] = b"opaque vendor module"
        self.package()
        response = self.review()
        self.assertEqual(response["status"], "partial")
        self.assertEqual(self.codes(response), ["ruc_configuration_unreadable"])
        self.assertEqual(response["result"]["rucReview"]["trafficManagers"], [])

    def test_duplicate_archive_member_cannot_be_selected_by_case(self):
        self.members[MAPPING.lower()] = self.members[MAPPING]
        self.package()
        self.assertEqual(self.codes(self.review()), ["duplicate_ruc_member"])

    def configure_display(self):
        cpu = self.project / "Physical/ARsim/PC"
        manifest = cpu / "Cpu.pkg"
        xml = manifest.read_text(encoding="utf-8")
        files = ("Display.vis", "Counter.binding", "Local.uaserver", "Exposure.uad", "Display.mappviewcfg")
        registration = "".join(f'<Object Type="File">{name}</Object>' for name in files)
        manifest.write_text(xml.replace("</Objects>", registration + "</Objects>"), encoding="utf-8")
        visu = """<vis:Visualization id="CounterPanel" xmlns:vis="http://www.br-automation.com/iat2015/visualizationDefinition/v2">
          <Contents><Content refId="DemoOverview"/></Contents>
          <BindingsSets><BindingsSet refId="Counter"/></BindingsSets>
        </vis:Visualization>"""
        saved = """<BindingsSet id="Counter" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
          <Bindings><Binding mode="oneWay">
            <Source xsi:type="opcUa" refId="::Pulse:Count" attribute="value"/>
            <Target xsi:type="brease" contentRefId="DemoOverview" widgetRefId="CountView" attribute="value"/>
          </Binding></Bindings>
        </BindingsSet>"""
        ua = """<Configuration><Element Type="UASERVER"><Group ID="DefaultOpcUaServer">
          <Property ID="Alias"/><Property ID="Hostname"/><Property ID="EndpointUrl"/>
          <Property ID="IPAddress" Value="127.0.0.1"/><Property ID="Port" Value="14840"/>
          <Property ID="DefaultNamespace" Value="urn:B&amp;R/pv/"/>
        </Group></Element></Configuration>"""
        source_map = """<OpcUaSource><DefaultView><Module>
          <Task Name="Pulse" Enable="True"><Variable Name="Count" Enable="True"/></Task>
        </Module></DefaultView></OpcUaSource>"""
        config = """<Configuration><Element Type="MAPPVIEWCFG"><Group ID="Client">
          <Property ID="defaultVisu" Value="CounterPanel"/>
        </Group></Element></Configuration>"""
        self.members[f"{SERVER}/Display.mappviewcfg"] = config.encode()
        for name, text in zip(files, (visu, saved, ua, source_map, config)):
            (cpu / name).write_text(text, encoding="utf-8")
        content = self.project / "Logical/mappView/Overview.content"
        xml = content.read_text(encoding="utf-8")
        content.write_text(xml.replace("</Widgets>", '<Widget xsi:type="widgets.brease.NumericOutput" id="CountView"/></Widgets>'),
                           encoding="utf-8")
        self.plan["manifest"]["inputs"] = source_tree(self.project)
        self.plan["sha256"] = identity(self.plan["manifest"])
        self.write("plan.json", self.plan)
        self.members[DISPLAY] = visu.replace('refId="Counter"', 'refId="2100"').encode()
        self.members[BINDING] = b"""<BindingDefinition xmlns="http://www.br-automation.com/iat2014/binding/runtime/v1"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
          <Content id="DemoOverview"><Binding mode="oneWay">
            <Source xsi:type="opcUa" refId="::Pulse:Count" attribute="value"/>
            <Target xsi:type="brease" refId="DemoOverview_CountView" attribute="value"/>
          </Binding></Content>
        </BindingDefinition>"""
        self.members[WIDGET] += b'<div id="DemoOverview_CountView" data-brease-widget="widgets/brease/NumericOutput"></div>'
        self.members[CONNECTION] = b'prefix<?xml version="1.0"?>' + ua.encode()
        node_map = b"""<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd"><DefaultView>
          <Module><Task Name="Pulse"><Variable Name="Count" DataType="i=6" AccessLevel="0x01"/></Task></Module>
        </DefaultView></UANodeSet>"""
        self.members[MAP] = compress(node_map)
        return node_map

    def binding_review(self, **options):
        return execute({"schemaVersion": 1, "workspace": "readback", "operation": "ruc_binding_review",
                        "arguments": {"project": "Loop/Loop.apj", "jobDirectory": "retained",
                                      "visualizationId": "CounterPanel", "contentId": "DemoOverview",
                                      "widgetId": "CountView", "stringIdentifier": "::Pulse:Count", **options}}, self.root)

    def test_readback_resolves_generated_binding_and_counter_map(self):
        self.configure_display()
        self.package()
        response = self.binding_review()
        self.assertEqual(response["status"], "complete", response)
        report = response["result"]["bindingReview"]
        self.assertEqual(report["compiled"]["binding"], BINDING)
        self.assertEqual(report["compiled"]["dataType"], "i=6")
        self.assertEqual(report["compiled"]["accessLevel"], "0x01")
        self.assertIn("::Pulse:Count → DemoOverview_CountView", markdown(response))

    def test_typed_binding_uses_registered_iec_program_and_checks_signedness(self):
        node_map = self.configure_display()
        package = self.project / "Logical/Package.pkg"
        package.write_bytes(package.read_bytes().replace(
            b"</Objects>", b'<Object Type="Package">Tasks</Object></Objects>'))
        program = self.project / "Logical/Tasks/Counter"
        program.mkdir(parents=True)
        (program.parent / "Package.pkg").write_bytes(
            b'<Package><Objects><Object Type="Program">Counter</Object></Objects></Package>')
        (program / "IEC.prg").write_bytes(
            b'<Program><Files><File Private="true">Variables.var</File></Files></Program>')
        variables = program / "Variables.var"
        variables.write_bytes(
            "(* Count : UDINT; *)\r\nVAR RETAIN\r\n    Count, Previous : DINT := 0;\r\nEND_VAR\r\n".encode("utf-16"))
        (program / "Unregistered.var").write_bytes(b"VAR\nCount : UDINT;\nEND_VAR\n")
        software = self.project / "Physical/ARsim/PC/Cpu.sw"
        software.write_bytes(b'<SwConfiguration><TaskClass Name="Cyclic#4">'
                             b'<Task Name="Pulse" Language="IEC" Source="Tasks.Counter.prg"/>'
                             b'</TaskClass></SwConfiguration>')
        self.plan["manifest"]["inputs"] = source_tree(self.project)
        self.plan["sha256"] = identity(self.plan["manifest"])
        self.write("plan.json", self.plan)
        self.package()

        response = self.binding_review(verifyIecType=True)
        self.assertEqual(response["status"], "complete", response)
        result = response["result"]
        declaration = result["bindingReview"]["saved"]["iecDeclaration"]
        self.assertEqual(declaration["program"], "Logical/Tasks/Counter/IEC.prg")
        self.assertEqual(declaration["taskClass"], "Cyclic#4")
        self.assertEqual((declaration["type"], declaration["line"], declaration["expectedDataType"]), ("DINT", 3, "i=6"))
        sources = {row["path"]: row["sha256"] for row in result["snapshot"]["files"]}
        self.assertEqual(sources[declaration["path"]], declaration["sha256"])
        self.assertNotIn("Logical/Tasks/Counter/Unregistered.var", sources)
        self.assertIn("IEC DINT · Logical/Tasks/Counter/Variables.var line 3", markdown(response))

        self.members[MAP] = compress(node_map.replace(b'DataType="i=6"', b'DataType="i=7"'))
        self.package()
        self.assertEqual(self.binding_review()["status"], "complete")
        failed = self.binding_review(verifyIecType=True)
        self.assertEqual(self.codes(failed), ["binding_readback_mismatch"])
        self.assertIn("Compiled node type i=7 differs from saved IEC DINT", failed["result"]["diagnostics"][0]["message"])

    def test_compiled_binding_source_is_checked_even_when_the_archive_is_valid(self):
        self.configure_display()
        self.members[BINDING] = self.members[BINDING].replace(b"::Pulse:Count", b"::Pulse:Other")
        self.package()
        self.assertEqual(self.review()["status"], "complete")
        response = self.binding_review()
        self.assertEqual(self.codes(response), ["binding_readback_mismatch"])
        self.assertEqual(response["result"]["diagnostics"][0]["path"], BINDING)
        self.assertNotEqual(response["result"]["diagnostics"][0].get("scope"), "source")

    def test_orphan_compiled_binding_cannot_prove_active_binding(self):
        self.configure_display()
        self.members[DISPLAY] = self.members[DISPLAY].replace(b'<BindingsSet refId="2100"/>', b"")
        self.package()
        response = self.binding_review()
        self.assertEqual(self.codes(response), ["binding_readback_mismatch"])
        self.assertEqual(response["result"]["diagnostics"][0]["path"], DISPLAY)

    def test_compiled_reverse_binding_is_rejected_as_second_connection(self):
        self.configure_display()
        reverse = b"""<Binding mode="oneWay">
          <Source xsi:type="brease" refId="DemoOverview_CountView" attribute="value"/>
          <Target xsi:type="opcUa" refId="::Pulse:Count" attribute="value"/>
        </Binding>"""
        self.members[BINDING] = self.members[BINDING].replace(b"</Content>", reverse + b"</Content>")
        self.package()
        response = self.binding_review()
        self.assertEqual(self.codes(response), ["binding_readback_mismatch"])
        self.assertIn("found 2", response["result"]["diagnostics"][0]["message"])

    def test_compiled_writable_node_or_changed_connection_is_rejected(self):
        node_map = self.configure_display()
        self.members[MAP] = compress(node_map.replace(b'AccessLevel="0x01"', b'AccessLevel="0x03"'))
        self.package()
        self.assertIn("differs from CurrentRead", self.binding_review()["result"]["diagnostics"][0]["message"])
        self.members[MAP] = compress(node_map)
        self.members[CONNECTION] = self.members[CONNECTION].replace(b"14840", b"14841")
        self.package()
        self.assertIn("endpoint differs", self.binding_review()["result"]["diagnostics"][0]["message"])

    def test_combined_hmi_requires_same_default_visualization_before_starting_arsim(self):
        self.configure_display()
        member = f"{SERVER}/Display.mappviewcfg"
        self.members[member] = self.members[member].replace(b"CounterPanel", b"OtherPanel")
        self.package()
        response = execute({"schemaVersion": 1, "workspace": "readback", "operation": "arsim_hmi_smoke",
                            "arguments": {"project": "Loop/Loop.apj", "jobDirectory": "retained",
                                          "visualizationId": "CounterPanel", "contentId": "DemoOverview",
                                          "widgetId": "CountView", "stringIdentifier": "::Pulse:Count",
                                          "pviTransfer": "unused.exe", "browserExecutable": "unused.exe",
                                          "sessionDirectory": "unused-session"}}, self.root)
        self.assertEqual(response["status"], "issues")
        self.assertEqual(response["result"]["arsimSmoke"]["state"], "not_started")
        self.assertEqual(response["result"]["diagnostics"][0]["path"], member)
        self.assertIn("default visualization must be", markdown(response))
        self.assertFalse((self.root / "unused-session").exists())
