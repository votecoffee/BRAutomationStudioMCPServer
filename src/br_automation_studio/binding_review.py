"""Read back a local AS4 mapp View value binding and its generated PLC node."""

from dataclasses import dataclass
from gzip import GzipFile
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zlib import error as CompressionError

from .project import ProjectXml, XML_LIMIT, package_files
from .ruc_review import CONTENT, DATA, VIEW, open_configuration

SERVER = VIEW.removesuffix("/wwwRoot") + "/server"
NODESETS = "FilesToTransfer/AddonsData/OPCUA/NodeSet/Generated"
VISUALIZATION = "{http://www.br-automation.com/iat2015/visualizationDefinition/v2}Visualization"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
NAMESPACE = "urn:B&R/pv/"
TASK_VARIABLE = re.compile(r"::([a-zA-Z_][a-zA-Z0-9_]*):([a-zA-Z_][a-zA-Z0-9_]*)")


class BindingProblem(ValueError):
    def __init__(self, path, message):
        super().__init__(message)
        self.path = Path(path)


def require(condition, path, message):
    if not condition:
        raise BindingProblem(path, message)


def one(items, path, label):
    items = list(items)
    require(len(items) == 1, path, f"Expected exactly one {label}, found {len(items)}")
    return items[0]


def nested(node, *names):
    prefix = node.tag.rpartition("}")[0]
    # The v2 visualization XSD qualifies the root only.
    prefix = prefix + "}" if prefix and node.tag != VISUALIZATION else ""
    level = [node]
    for name in names:
        level = [item for parent in level for item in parent.findall(prefix + name)]
    return level


def default_visualizations(root):
    return [item.get("Value") for element in nested(root, "Element") if element.get("Type") == "MAPPVIEWCFG"
            for group in nested(element, "Group") if group.get("ID") == "Client"
            for item in nested(group, "Property") if item.get("ID") == "defaultVisu"]


def saved_documents(source, paths, suffix, root):
    for path in paths:
        if path.suffix.lower() == suffix:
            node, _ = source.xml(path, root)
            require(node is not None, path, f"Could not read the saved {root} document")
            yield path, node


def unpack_xml(data, path, expected):
    try:
        root = ET.fromstring(data, parser=ET.XMLParser(target=ProjectXml()))
    except (ET.ParseError, LookupError, ValueError) as error:
        raise BindingProblem(path, f"Could not parse packaged XML: {error}") from error
    require(root.tag == expected, path, "Unexpected packaged XML schema")
    return root


def packaged_bytes(archive, path):
    member = archive.member(path)
    require(member is not None, path, "Could not select the compiled package member")
    try:
        return archive.read(member)
    except ValueError as error:
        raise BindingProblem(path, str(error)) from error


class RenderedWidget(HTMLParser):
    def __init__(self, dom_id):
        super().__init__(convert_charrefs=True)
        self.dom_id = dom_id
        self.matches = []

    def handle_starttag(self, name, attrs):
        ids = [value for key, value in attrs if key == "id"]
        if self.dom_id in ids:
            widget = [value for key, value in attrs if key == "data-brease-widget"]
            self.matches.append(widget[0] if len(ids) == 1 and len(widget) == 1 else None)


def connection(root, path):
    group = one((group for item in nested(root, "Element") if item.get("Type") == "UASERVER"
                 for group in nested(item, "Group") if group.get("ID") == "DefaultOpcUaServer"),
                path, "default OPC UA server group")
    properties = {}
    for key in ("Alias", "Hostname", "IPAddress", "Port", "EndpointUrl", "DefaultNamespace"):
        entry = one((item for item in nested(group, "Property") if item.get("ID") == key), path, f"{key} property")
        properties[key] = entry.get("Value") or ""
    port = properties["Port"]
    require(properties["IPAddress"] == "127.0.0.1" and not any(
        properties[key] for key in ("Hostname", "Alias", "EndpointUrl")
    ) and re.fullmatch(r"[0-9]{1,5}", port) and 1024 <= int(port) <= 65535, path,
            "Expected an explicit local default OPC UA connection with a numeric unprivileged port")
    require(properties["DefaultNamespace"] == NAMESPACE, path, f"Expected default namespace {NAMESPACE!r}")
    return {"host": properties["IPAddress"], "port": int(port), "namespaceUri": properties["DefaultNamespace"]}


def variable_matches(root, task, variable):
    return [(item, field) for item in nested(root, "DefaultView", "Module", "Task") if item.get("Name") == task
            for field in nested(item, "Variable") if field.get("Name") == variable]


@dataclass(frozen=True)
class BindingTarget:
    visualization: str
    content: str
    widget: str
    identifier: str
    require_default: bool = False

    @property
    def dom_id(self):
        return f"{self.content}_{self.widget}"

    def receipt(self):
        return {"state": "not_checked", "visualizationId": self.visualization, "contentId": self.content,
                "widgetId": self.widget, "domId": self.dom_id, "stringIdentifier": self.identifier,
                "saved": {}, "compiled": {}}

    def touches(self, binding, compiled):
        for end in nested(binding, "Source") + nested(binding, "Target"):
            if end.get("attribute") != "value":
                continue
            if compiled and end.get("refId") == self.dom_id and end.get(XSI_TYPE) != "opcUa":
                return True
            if not compiled and (end.get("contentRefId"), end.get("widgetRefId")) == (self.content, self.widget):
                return True
        return False

    def check_membership(self, visu, path):
        require(sum(item.get("refId") == self.content for item in nested(visu, "Contents", "Content")) == 1,
                path, f"Expected one visualization reference to content {self.content!r}")

    def check_binding(self, matches, visualization_path, compiled):
        path, binding = one(matches, visualization_path, f"active binding for {self.dom_id!r} value")
        source = one(nested(binding, "Source"), path, "binding source")
        target = one(nested(binding, "Target"), path, "binding target")
        require(binding.get("mode") == "oneWay" and source.get(XSI_TYPE) == "opcUa"
                and source.get("refId") == self.identifier and source.get("attribute") == "value"
                and not source.get("serverAlias") and not source.get("namespace")
                and target.get(XSI_TYPE) == "brease" and target.get("attribute") == "value", path,
                f"Expected a one-way default OPC UA binding from {self.identifier!r} to {self.dom_id!r} value")
        if compiled:
            require(target.get("refId") == self.dom_id, path, "Compiled binding has a different widget target")
        else:
            require((target.get("contentRefId"), target.get("widgetRefId")) == (self.content, self.widget),
                    path, "Saved binding has a different widget target")
        return path

    def saved_binding(self, source, paths, output):
        path, visu = one(((path, node) for path, node in saved_documents(source, paths, ".vis", "Visualization")
                          if node.get("id") == self.visualization), ".", f"saved visualization {self.visualization!r}")
        require(visu.tag == VISUALIZATION, path, "Unexpected saved visualization schema")
        self.check_membership(visu, path)
        sets = list(saved_documents(source, paths, ".binding", "BindingsSet"))
        matches = []
        for ref in nested(visu, "BindingsSets", "BindingsSet"):
            name = ref.get("refId")
            candidate, root = one(((file, node) for file, node in sets if name and node.get("id") == name),
                                  path, f"saved binding set {name!r}")
            matches.extend((candidate, item) for item in nested(root, "Bindings", "Binding") if self.touches(item, False))
        binding = self.check_binding(matches, path, False)
        output.update(visualization=path.as_posix(), binding=binding.as_posix())

    def compiled_binding(self, archive, output):
        visu_path = f"{SERVER}/{self.visualization}.vis"
        visu = unpack_xml(packaged_bytes(archive, visu_path), visu_path, VISUALIZATION)
        require(visu.get("id") == self.visualization, visu_path, "Compiled visualization ID differs")
        self.check_membership(visu, visu_path)
        matches = []
        for ref in nested(visu, "BindingsSets", "BindingsSet"):
            name = ref.get("refId") or ""
            binding = archive.diagnostics.member(Path(visu_path), f"{name}.binding")
            require(name and binding is not None, visu_path, "Invalid compiled binding set reference")
            binding_path = binding.as_posix()
            root = unpack_xml(packaged_bytes(archive, binding_path), binding_path,
                              "{http://www.br-automation.com/iat2014/binding/runtime/v1}BindingDefinition")
            for content in nested(root, "Content"):
                for item in nested(content, "Binding"):
                    if self.touches(item, True):
                        require(content.get("id") == self.content, binding, "Selected widget binding is in a different content group")
                        matches.append((binding, item))
        binding = self.check_binding(matches, visu_path, True)
        output.update(visualization=visu_path, binding=binding.as_posix())

    def check_default(self, root, path):
        require(default_visualizations(root) == [self.visualization], path,
                f"The default visualization must be {self.visualization!r} for the browser check")

    def check(self, archive, source, manifest, report):
        try:
            reference = TASK_VARIABLE.fullmatch(self.identifier)
            require(reference is not None, ".", "Select a V1 OPC UA task scalar in ::Task:Variable form")
            task, variable = reference.groups()
            for value in (self.visualization, self.content, self.widget):
                require(re.fullmatch(r"[a-zA-Z0-9_-]+", value), ".", f"Unsupported resource ID {value!r}")
            cpu = Path("Physical", manifest["configuration"], manifest["cpu"], "Cpu.pkg")
            extensions = {"vis", "binding", "uaserver", "uad"}
            if self.require_default:
                extensions.add("mappviewcfg")
            paths = package_files(source, cpu, "Cpu", extensions)
            saved, compiled = report["saved"], report["compiled"]

            report["state"] = "checking_saved"
            self.saved_binding(source, paths, saved)
            contents = package_files(source, Path("Logical/Package.pkg"), "Package", {"content"})
            path, content = one(((path, node) for path, node in saved_documents(source, contents, ".content", "Content")
                                 if node.get("id") == self.content), cpu, f"saved content {self.content!r}")
            widget = one((item for item in nested(content, "Widgets", "Widget") if item.get("id") == self.widget),
                         path, f"saved widget {self.widget!r}")
            widget_type = widget.get(XSI_TYPE, "")
            require(widget_type.startswith("widgets."), path, "Missing supported saved widget type")
            saved.update(content=path.as_posix(), widgetType=widget_type)
            conn_path, conn_root = one(saved_documents(source, paths, ".uaserver", "Configuration"),
                                       cpu, "saved OPC UA connection document")
            saved.update(connection=conn_path.as_posix(), endpoint=connection(conn_root, conn_path))
            map_path, task_node, field = one(((path, item, field)
                                             for path, root in saved_documents(source, paths, ".uad", "OpcUaSource")
                                             for item, field in variable_matches(root, task, variable)),
                                            cpu, f"saved default-view task variable {self.identifier!r}")
            require(task_node.get("Enable") == field.get("Enable") == "True", map_path, "Saved task variable is disabled")
            saved["nodeMap"] = map_path.as_posix()
            if self.require_default:
                config, root = one(saved_documents(source, paths, ".mappviewcfg", "Configuration"),
                                   cpu, "saved mapp View configuration")
                self.check_default(root, config)
                saved.update(defaultVisualization=self.visualization, serverConfig=config.as_posix())

            report["state"] = "checking_compiled"
            self.compiled_binding(archive, compiled)
            if self.require_default:
                server_config = f"{SERVER}/{config.name}"
                candidates = [name for name in archive.members
                              if name.startswith(f"{SERVER.casefold()}/") and name.endswith(".mappviewcfg")]
                require(candidates == [server_config.casefold()], server_config,
                        "Could not select one compiled mapp View server configuration")
                root = unpack_xml(packaged_bytes(archive, server_config), server_config, "Configuration")
                self.check_default(root, server_config)
                compiled.update(defaultVisualization=self.visualization, serverConfig=server_config)
            html_path = f"{CONTENT}/{self.content}.html"
            widget_parser = RenderedWidget(self.dom_id)
            widget_parser.feed(packaged_bytes(archive, html_path).decode("utf-8-sig"))
            widget_parser.close()
            class_name = one(widget_parser.matches, html_path, f"rendered widget {self.dom_id!r}")
            require(class_name == widget_type.replace(".", "/"), html_path, "Compiled widget type differs")
            compiled.update(content=html_path, widgetType=class_name)
            conn_path = f"{DATA}/OpcUaSrv.br"
            try:
                conn_root = open_configuration(packaged_bytes(archive, conn_path))
            except (ET.ParseError, LookupError, ValueError) as error:
                raise BindingProblem(conn_path, f"Could not read packaged OPC UA configuration: {error}") from error
            compiled.update(connection=conn_path, endpoint=connection(conn_root, conn_path))
            require(compiled["endpoint"] == saved["endpoint"], conn_path, "Compiled default OPC UA endpoint differs")
            map_member = f"{NODESETS}/{map_path.stem}.uar.gz"
            try:
                with GzipFile(fileobj=BytesIO(packaged_bytes(archive, map_member))) as stream:
                    xml = stream.read(XML_LIMIT + 1)
            except (OSError, EOFError, CompressionError) as error:
                raise BindingProblem(map_member, f"Could not decompress compiled OPC UA node map: {error}") from error
            require(len(xml) <= XML_LIMIT, map_member, "Compiled OPC UA node map exceeds the read limit")
            root = unpack_xml(xml, map_member, "{http://opcfoundation.org/UA/2011/03/UANodeSet.xsd}UANodeSet")
            _, node = one(variable_matches(root, task, variable), map_member, f"compiled task variable {self.identifier!r}")
            compiled.update(nodeMap=map_member, dataType=node.get("DataType"), accessLevel=node.get("AccessLevel"))
            require(re.fullmatch(r"i=(?:[2-9]|10|11)", compiled["dataType"] or "") and len(node) == 0,
                    map_member, "Compiled variable is outside the supported scalar numeric node types")
            require(compiled["accessLevel"] == "0x01", map_member, "Compiled variable access differs from CurrentRead alone")
            report["state"] = "matched"
        except BindingProblem as error:
            diagnostics = archive.diagnostics if report["state"] == "checking_compiled" else source
            diagnostics.issue("error", "binding_readback_mismatch", error.path, str(error))
