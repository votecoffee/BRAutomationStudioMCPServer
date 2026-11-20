
"""Check saved assembly, traffic and mapp View references for a selected CPU."""

from collections import defaultdict
from .project import children, tag

ASSEMBLY_WIDGET = "widgets.mapp6D.Assembly6D"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def fields(node, name, field="Property"):
    return [child for child in children(node, field)
            if (child.get("ID") or "").split("[", 1)[0] == name]


def property_value(node, name):
    matches = fields(node, name)
    return matches[0].get("Value") if matches else None


def unique_property(node, name):
    matches = fields(node, name)
    return matches[0].get("Value") if len(matches) == 1 else None


def object_position(path, node, name, kind):
    positions = fields(node, "Position", "Selector")
    if len(positions) != 1:
        return {"name": name, "type": kind, "mode": None, "xy": [None, None], "path": path.as_posix()}
    position = positions[0]
    xy = [unique_property(position, axis) for axis in ("X", "Y")]
    return {"name": name, "type": kind, "mode": position.get("Value"), "xy": xy, "path": path.as_posix()}


def segment_layout(path, node):
    groups = fields(node, "Segments", "Group")
    segments = []
    if len(groups) == 1:
        for item in children(groups[0], "Group"):
            segments.append({"id": item.get("ID"), "name": unique_property(item, "Name"),
                             "typeCode": unique_property(item, "TypeGrid240"),
                             "gridXY": [unique_property(item, axis) for axis in ("X", "Y")],
                             "master": unique_property(item, "IsMaster")})
    origins = fields(node, "UserOrigin", "Group")
    origin = [unique_property(origins[0], axis) for axis in ("OffsetX", "OffsetY")] if len(origins) == 1 else None
    return {"layoutId": node.get("ID"), "path": path.as_posix(), "gridCode": unique_property(node, "GridSize"),
            "userOriginRaw": origin, "segments": segments}


class MotionLinks:
    def __init__(self, source):
        self.source = source
        self.entries = defaultdict(list)
        self.module_inventory = []

    def add(self, path, kind, node):
        elements = children(node, "Element")
        supported = [item for item in elements if item.get("Type") == kind and item.get("ID")]
        if len(supported) != len(elements) or not supported:
            self.source.issue("warning", "unsupported_module", path,
                              f"Expected named {kind} elements in this file")
        for element in supported:
            name = element.get("ID")
            self.module_inventory.append({"id": name, "type": kind, "path": path.as_posix()})
            self.entries[kind, name].append((path, element))
            if len(self.entries[kind, name]) == 2:
                self.source.issue("error", "duplicate_module", path, f"Duplicate {kind} {name!r}")

    def resolve(self, path, kind, identity, severity="error"):
        matches = self.entries.get((kind, identity), ())
        if len(matches) == 1:
            return matches[0]
        reason = "ambiguous" if matches else "unresolved"
        self.source.issue(severity, f"{reason}_reference", path, f"{reason.title()} {kind} {identity!r}")
        return None

    def targets(self, path, node, group, property_name, kind, required=True):
        selectors = [item for section in fields(node, group, "Group")
                     for item in fields(section, property_name)]
        if required and not selectors:
            self.source.issue("warning", "missing_link", path, f"No {property_name} is declared")
        results = []
        for selector in selectors:
            identity = selector.get("Value")
            match = self.resolve(path, kind, identity)
            if match:
                results.append(match)
        return results

    def check(self, geometry_manager=None, *, include_layout=False):
        assembly_links = []
        managers = []
        for (kind, identity), entries in self.entries.items():
            if len(entries) != 1:
                continue
            path, node = entries[0]
            if kind == "assembly6d":
                layouts = self.targets(path, node, "Layout", "Layout6D", "layout6d")
                assembly = {"assembly": identity, "layouts": [item.get("ID") for _, item in layouts]}
                if include_layout:
                    assembly["layoutEvidence"] = [segment_layout(file, item) for file, item in layouts]
                assembly_links.append(assembly)
            if kind == "trafficmanager":
                managers.append(self.manager(path, node, collect_positions=identity == geometry_manager))
        return assembly_links, managers

    def manager(self, path, node, *, collect_positions=False):
        bases = fields(node, "Base", "Selector")
        assembly = None
        if len(bases) == 1 and bases[0].get("Value") == "AssemblyAcp6D":
            assembly = property_value(bases[0], "BaseRef")
            self.resolve(path, "assembly6d", assembly)
        else:
            self.source.issue("warning", "unsupported_manager_base", path, "Expected one AssemblyAcp6D base")

        object_lists = self.targets(path, node, "TrafficObjects", "TrafficObjectListRef", "trafficobjectlist")
        mapping_lists = self.targets(path, node, "TrafficMapping", "TrafficMappingListRef", "trafficmappinglist")
        rule_lists = self.targets(path, node, "TrafficRules", "TrafficRuleListRef", "trafficrulelist", required=False)
        objects = {}
        positions = []
        for list_path, object_list in object_lists:
            for item in fields(object_list, "TrafficObject", "Selector"):
                identity = property_value(item, "Name")
                object_type = self.selector_type(list_path, item)
                if not identity or not object_type:
                    self.source.issue("warning", "unsupported_traffic_object", list_path,
                                      "Traffic object has no supported name or type")
                elif identity in objects:
                    self.source.issue("error", "duplicate_traffic_object", list_path,
                                      f"Duplicate traffic object {identity!r}")
                    objects[identity] = None
                else:
                    objects[identity] = object_type
                    if collect_positions:
                        positions.append(object_position(list_path, item, identity, object_type))

        edges = []
        for list_path, mapping in mapping_lists:
            edges.extend(self.edges(list_path, mapping, objects))
        result = {
            "manager": node.get("ID"), "assembly": assembly,
            "objectLists": [item.get("ID") for _, item in object_lists],
            "mappingLists": [item.get("ID") for _, item in mapping_lists],
            "ruleLists": [item.get("ID") for _, item in rule_lists],
            "objects": [{"name": name, "type": kind} for name, kind in objects.items()],
            "edges": edges,
        }
        if collect_positions:
            result["positionEvidence"] = positions
        return result

    def selector_type(self, path, selector):
        value = selector.get("Value")
        declared = property_value(selector, "Type")
        if declared and value and declared != value:
            self.source.issue("error", "conflicting_type", path,
                              f"Selector value {value!r} conflicts with Type {declared!r}")
            return None
        return declared or value

    def edges(self, path, mapping, objects):
        for group in fields(mapping, "TrafficMapping", "Group"):
            sources = fields(group, "Source", "Selector")
            if len(sources) != 1:
                self.source.issue("warning", "unsupported_mapping", path, "Expected one source selector per mapping")
                continue
            source = sources[0]
            start = property_value(source, "SourceRef")
            start_type = self.selector_type(path, source)
            self.check_endpoint(path, objects, start, start_type)
            for section in fields(source, "Destinations", "Group"):
                for destination in fields(section, "To", "Selector"):
                    end = property_value(destination, "DestinationRef")
                    end_type = self.selector_type(path, destination)
                    self.check_endpoint(path, objects, end, end_type)
                    yield {"source": start, "sourceType": start_type, "destination": end, "destinationType": end_type}

    def check_endpoint(self, path, objects, name, expected):
        actual = objects.get(name)
        if not expected or actual != expected:
            self.source.issue("error", "invalid_mapping_endpoint", path,
                              f"Endpoint {name!r} expects {expected!r}, found {actual!r} in this manager")

    def widgets(self, paths, managers):
        bases = {row["manager"]: row["assembly"] for row in managers}
        for path in paths:
            node, _ = self.source.xml(path, "Content")
            if node is None:
                continue
            for widget in node.iter():
                if tag(widget) != "Widget" or widget.get(XSI_TYPE) != ASSEMBLY_WIDGET:
                    continue
                identity = widget.get("id")
                assembly = widget.get("mpLink")
                manager = widget.get("mpLinkTrafficManager")
                if assembly:
                    self.resolve(path, "assembly6d", assembly, "warning")
                else:
                    self.source.issue("warning", "unbound_widget", path, f"Widget {identity!r} has no assembly link")
                if manager:
                    manager_id = manager.removeprefix("mapp6D::")
                    matched = self.resolve(path, "trafficmanager", manager_id, "warning")
                    if matched and assembly and bases[manager_id] and bases[manager_id] != assembly:
                        self.source.issue("error", "widget_link_mismatch", path,
                                          f"Widget {identity!r} and manager {manager_id!r} refer to different assemblies")
                yield {"id": identity, "path": path.as_posix(), "contentId": node.get("id"),
                       "assembly": assembly, "manager": manager}
