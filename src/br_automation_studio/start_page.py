"""Read back the default mapp View page that places a selected Assembly6D widget."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .binding_review import (
    SERVER, VISUALIZATION, BindingProblem, RenderedWidget, default_visualizations,
    nested, one, packaged_bytes, require, saved_documents, unpack_xml,
)
from .inspection import inspect_saved, registered_assembly
from .json_io import invalid_constant, unique_object
from .project import SavedFiles, children, package_files, tag
from .ruc_review import VIEW, blank_equivalent


RESOURCE_ID = re.compile(r"[a-zA-Z0-9_-]+")
PAGE = "{http://www.br-automation.com/iat2015/pageDefinition/v2}Page"
LAYOUT = "{http://www.br-automation.com/iat2015/layoutDefinition/v2}Layout"
WIDGET_TYPE = "widgets/mapp6D/Assembly6D"
SAVED_CONTENT = "{http://www.br-automation.com/iat2015/contentDefinition/v2}Content"


def resource_id(value, path, label):
    require(isinstance(value, str) and RESOURCE_ID.fullmatch(value), path, f"Invalid {label} {value!r}")
    return value


def saved_resource(source, paths, extension, root, identity, owner):
    label = root.rsplit("}", 1)[-1]
    path, node = one(((path, node) for path, node in saved_documents(source, paths, extension, label)
                      if node.get("id") == identity), owner, f"saved {label} {identity!r}")
    require(node.tag == root, path, f"Unexpected saved {label} schema")
    return path, node


def visualization_start(visu, path, visualization, content=None):
    require(visu.get("id") == visualization, path, "Visualization ID differs")
    start = one(nested(visu, "StartPage"), path, "StartPage element")
    page = resource_id(start.get("pageRefId"), path, "start page ID")
    references = [("Pages", "Page", page)]
    if content is not None:
        references.append(("Contents", "Content", content))
    for group, tag, identity in references:
        one((item for item in nested(visu, group, tag) if item.get("refId") == identity),
            path, f"visualization reference to {identity!r}")
    return page


def saved_content_placement(source, vis_path, visu, visualization, content):
    page = visualization_start(visu, vis_path, visualization, content)
    logical = package_files(source, Path("Logical/Package.pkg"), "Package", {"page", "layout"})
    page_path, page_node = saved_resource(source, logical, ".page", PAGE, page, vis_path)
    layout = resource_id(page_node.get("layoutRefId"), page_path, "layout ID")
    placements = page_node.findall("./Assignments/Assignment")
    wanted = one((row for row in placements if row.get("baseContentRefId") == content),
                 page_path, f"start page assignment for {content!r}")
    area = resource_id(wanted.get("areaRefId"), page_path, "layout area ID")
    require(wanted.get("type") == "Content", page_path, "Expected a direct Content assignment")
    one((row for row in placements if row.get("areaRefId") == area), page_path, f"assignment to area {area!r}")
    layout_path, layout_node = saved_resource(source, logical, ".layout", LAYOUT, layout, page_path)
    one((row for row in layout_node.findall("./Areas/Area") if row.get("id") == area),
        layout_path, f"layout area {area!r}")
    return {"pageId": page, "page": page_path.as_posix(), "layoutId": layout,
            "layout": layout_path.as_posix(), "areaId": area}


def saved_start(source, manifest, widget):
    cpu = Path("Physical", manifest["configuration"], manifest["cpu"], "Cpu.pkg")
    physical = package_files(source, cpu, "Cpu", {"mappviewcfg", "vis"})
    server, config = one(saved_documents(source, physical, ".mappviewcfg", "Configuration"),
                         cpu, "saved mapp View server configuration")
    visualization = resource_id(one(default_visualizations(config), server, "default visualization"),
                                server, "default visualization ID")
    vis_path, visu = saved_resource(source, physical, ".vis", VISUALIZATION, visualization, server)
    content = widget["contentId"]
    placement = saved_content_placement(source, vis_path, visu, visualization, content)
    return {"serverConfig": server.as_posix(), "visualizationId": visualization, "visualization": vis_path.as_posix(),
            **placement, "contentId": content, "content": widget["path"]}


def json_object(parent, key, path):
    value = parent.get(key)
    require(isinstance(value, dict), path, f"Expected client map object {key!r}")
    return value


def read_client_map(archive, path):
    try:
        value = json.loads(packaged_bytes(archive, path).decode("utf-8-sig"),
                           object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (UnicodeError, ValueError) as error:
        raise BindingProblem(path, f"Could not read the generated client map: {error}") from error
    require(isinstance(value, dict), path, "Expected a generated client map object")
    return value


def compiled_start(archive, saved, rendered, widget_id):
    visualization, page, layout, area, content = (saved[key] for key in
                                                  ("visualizationId", "pageId", "layoutId", "areaId", "contentId"))
    server = f"{SERVER}/{Path(saved['serverConfig']).name}"
    candidates = [key for key in archive.members if key.startswith(f"{SERVER.casefold()}/")
                  and "/" not in key.removeprefix(f"{SERVER.casefold()}/") and key.endswith(".mappviewcfg")]
    require(candidates == [server.casefold()], server, "Could not select the matching compiled mapp View server configuration")
    config = unpack_xml(packaged_bytes(archive, server), server, "Configuration")
    require(default_visualizations(config) == [visualization], server, "Compiled default visualization differs")
    vis_path = f"{SERVER}/{visualization}.vis"
    visu = unpack_xml(packaged_bytes(archive, vis_path), vis_path, VISUALIZATION)
    require(visualization_start(visu, vis_path, visualization, content) == page, vis_path, "Compiled start page differs")

    map_path = f"{VIEW}/{visualization}.json"
    client = read_client_map(archive, map_path)
    require(client.get("startPage") == page, map_path, "Generated client start page differs")
    page_node = json_object(json_object(client, "pages", map_path), page, map_path)
    require(page_node.get("layout") == layout, map_path, "Generated start page layout differs")
    assignments = page_node.get("assignments")
    require(isinstance(assignments, list) and all(isinstance(row, dict) for row in assignments),
            map_path, "Expected generated page assignments")
    placement = one((row for row in assignments if row.get("contentId") == content),
                    map_path, f"generated assignment for {content!r}")
    require(placement.get("areaId") == area and placement.get("type") == "Content",
            map_path, "Generated direct content assignment differs")
    one((row for row in assignments if row.get("areaId") == area), map_path, f"generated assignment to area {area!r}")
    layout_node = json_object(json_object(client, "layouts", map_path), layout, map_path)
    json_object(json_object(layout_node, "areas", map_path), area, map_path)
    definition = json_object(json_object(client, "contents", map_path), content, map_path)
    html_path = rendered["path"]
    url = html_path.removeprefix(VIEW)
    require(definition.get("path") == url, map_path, "Generated content URL differs from the reviewed RUC member")
    parser = RenderedWidget(widget_id)
    parser.feed(packaged_bytes(archive, html_path).decode("utf-8-sig"))
    parser.close()
    require(parser.matches == [WIDGET_TYPE], html_path, "Expected one DOM element with the reviewed Assembly6D class")
    return {"serverConfig": server, "visualization": vis_path, "clientMap": map_path,
            "clientMapUrl": map_path.removeprefix(VIEW).lower(), "content": html_path, "contentUrl": url, "widgetType": WIDGET_TYPE,
            "assembly": rendered["assembly"], "manager": rendered["manager"]}


def select_saved_widget(widgets, widget_id):
    candidates = [(f"{row['contentId']}_{row['id']}", row) for row in widgets]
    return one((item for item in candidates if widget_id is None or item[0] == widget_id),
               ".", "selected saved Assembly6D widget")


def require_widget_links(dom_id, widget, assembly, manager):
    require(assembly and widget["assembly"] == assembly and
            widget["manager"] in (manager, f"mapp6D::{manager}"), widget["path"],
            f"Assembly6D widget {dom_id!r} must show reviewed Traffic Manager {manager!r} and assembly {assembly!r}")


@dataclass(frozen=True)
class StartPageWidget:
    dom_id: str | None

    def receipt(self):
        return {"state": "not_checked", "domId": self.dom_id}

    def check_saved(self, source, manifest, widgets, report, route_manager=None):
        dom_id, widget = select_saved_widget(widgets, self.dom_id)
        report["domId"] = dom_id
        if route_manager is not None:
            require_widget_links(dom_id, widget, route_manager["assembly"], route_manager["manager"])
        report["saved"] = saved_start(source, manifest, widget)
        pinned = {row["path"]: row["sha256"] for row in manifest["inputs"]["files"]}
        snapshot = report["savedReadSet"] = source.snapshot()
        require(not source.diagnostics and all(pinned.get(row["path"]) == row["sha256"] for row in snapshot["files"]),
                ".", "Saved start page metadata could not be verified against the build manifest")
        return widget

    def check(self, archive, project_root, manifest, saved, rendered, report, route_manager=None):
        source = SavedFiles(project_root)
        report["state"] = "unverified"
        try:
            widget = self.check_saved(source, manifest, saved, report, route_manager)
            dom_id = report["domId"]
            compiled = one((row for row in rendered if (row["contentId"], row["id"]) == (widget["contentId"], widget["id"])),
                           widget["path"], f"rendered Assembly6D options for {dom_id!r}")
            require(compiled["className"] == WIDGET_TYPE and
                    all(compiled[key] in blank_equivalent(widget[key]) for key in ("assembly", "manager")),
                    compiled["path"], "Rendered Assembly6D class or links differ from the saved widget")
            report["compiled"] = compiled_start(archive, report["saved"], compiled, dom_id)
            report["state"] = "matched"
        except BindingProblem as error:
            archive.diagnostics.issue("error", "start_page_widget_mismatch", error.path, str(error))
        archive.diagnostics.diagnostics.extend(dict(issue, scope="start_page_source") for issue in source.diagnostics)


def check_registered_manager_base(source, project, manager_id, assembly_id):
    module = one((row for row in project["motionModules"] if row["type"] == "trafficmanager"
                  and row["id"] == manager_id), project["project"], f"registered Traffic Manager {manager_id!r}")
    path = Path(module["path"])
    root, _ = source.xml(path, "Configuration")
    manager = one((node for node in children(root, "Element") if node.get("ID") == manager_id
                   and node.get("Type") == "trafficmanager"), path, f"Traffic Manager element {manager_id!r}")
    selector = one((node for node in children(manager, "Selector") if node.get("ID") == "Base"),
                   path, "Traffic Manager Base selector")
    require(selector.get("Value") == "AssemblyAcp6D", path, "The Traffic Manager must use an ACOPOS 6D assembly base")
    base = one((node for node in children(selector, "Property") if node.get("ID") == "BaseRef"),
               path, "Traffic Manager BaseRef")
    require(base.get("Value") == assembly_id, path,
            f"Traffic Manager {manager_id!r} must also use assembly {assembly_id!r}")
    return path


def check_start_widget_content(source, widget):
    content_id = resource_id(widget["contentId"], widget["path"], "content ID")
    widget_id = resource_id(widget["id"], widget["path"], "widget ID")
    paths = package_files(source, Path("Logical/Package.pkg"), "Package", {"content"})
    path, content = saved_resource(source, paths, ".content", SAVED_CONTENT, content_id, widget["path"])
    require(path.as_posix() == widget["path"], path, "The start page content differs from the selected widget's content")
    one((node for node in content.iter() if tag(node) == "Widget" and node.get("id") == widget_id),
        path, f"widget {widget_id!r} in its saved content")


def review_saved_assembly_widget(path, configuration, cpu, widget_id, assembly_id, manager_id):
    source = SavedFiles(path.parent)
    result = inspect_saved(source, Path(path.name), configuration, cpu)
    review = {"state": "unverified", "domId": widget_id, "expectedAssembly": assembly_id,
              "expectedManager": manager_id, "saved": {}}
    result["savedAssemblyWidgetReview"] = review
    if result["selectedCpu"]:
        try:
            dom_id, widget = select_saved_widget(result["widgets"], widget_id)
            review["domId"] = dom_id
            review["widget"] = widget
            check_start_widget_content(source, widget)
            selection = {"configuration": result["selectedConfiguration"], "cpu": result["selectedCpu"]}
            review["saved"] = saved_start(source, selection, widget)
            require_widget_links(dom_id, widget, assembly_id, manager_id)
            assembly_path, _ = registered_assembly(source, result, assembly_id)
            require(assembly_path is not None, widget["path"], f"Expected one registered assembly {assembly_id!r}")
            review["assemblyPath"] = assembly_path.as_posix()
            review["managerPath"] = check_registered_manager_base(source, result, manager_id, assembly_id).as_posix()
        except BindingProblem as error:
            review["state"] = "mismatched"
            source.issue("error", "saved_assembly_widget_mismatch", error.path, str(error))
        else:
            review["state"] = "matched"
    result["snapshot"] = source.snapshot()
    return result
