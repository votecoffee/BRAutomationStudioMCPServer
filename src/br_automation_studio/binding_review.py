"""Read back a local AS4 mapp View value binding and its generated PLC node."""

from dataclasses import dataclass, replace
from gzip import GzipFile
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zlib import error as CompressionError

from .global_variables import GLOBAL_LIMIT
from .hardware_timing import parameter_values, selected_cpu_module, single_value, unsigned_value
from .inspection import inspect_saved
from .motion_interfaces import declared_members, lexical_view
from .project import ProjectXml, SavedFiles, XML_LIMIT, children, choices, package_files
from .ruc_review import CONTENT, DATA, VIEW, open_configuration

SERVER = VIEW.removesuffix("/wwwRoot") + "/server"
NODESETS = "FilesToTransfer/AddonsData/OPCUA/NodeSet/Generated"
VISUALIZATION = "{http://www.br-automation.com/iat2015/visualizationDefinition/v2}Visualization"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
NAMESPACE = "urn:B&R/pv/"
TASK_VARIABLE = re.compile(r"::([a-zA-Z_][a-zA-Z0-9_]*):([a-zA-Z_][a-zA-Z0-9_]*)")
VAR_BLOCK = re.compile(r"^[ \t]*VAR(?:[ \t]+(?:CONSTANT|RETAIN|NON_RETAIN))?[ \t]*\n"
                       r"(.*?)^[ \t]*END_VAR[ \t]*;?[ \t]*$", re.I | re.M | re.S)
IEC_NODE_TYPES = {
    "SINT": "i=2", "USINT": "i=3", "INT": "i=4", "UINT": "i=5", "DINT": "i=6", "UDINT": "i=7",
    "LINT": "i=8", "ULINT": "i=9", "REAL": "i=10", "LREAL": "i=11",
}


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


def task_program(source, software, task):
    root, _ = source.xml(software, "SwConfiguration")
    require(root is not None, software, "Could not read the selected CPU's task assignments")
    group, assignment = one(((group, row) for group in children(root, "TaskClass") for row in children(group, "Task")
                             if (row.get("Name") or "").casefold() == task.casefold()),
                            software, f"CPU task assignment for {task!r}")
    reference = (assignment.get("Source") or "").split(".")
    require(assignment.get("Language") == "IEC" and len(reference) >= 2 and reference[-1].casefold() == "prg"
            and all(reference), software, f"Expected a registered IEC program source for task {task!r}")

    package = Path("Logical/Package.pkg")
    for name, kind in zip(reference[:-1], ("Package",) * (len(reference) - 2) + ("Program",)):
        root, _ = source.xml(package, "Package")
        require(root is not None, package, "Could not read the package for the selected IEC program")
        folder = choices(source, package, root, kind).get(name.casefold())
        require(folder is not None, package, f"No registered {kind.lower()} {name!r} for task {task!r}")
        package = folder / "Package.pkg"
    return folder / "IEC.prg", group.get("Name")


def read_local_variables(source, path):
    raw = source.read_bytes(path, GLOBAL_LIMIT, label="IEC task declaration file")
    require(raw is not None, path, "Could not read the registered task declaration file")
    try:
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = lexical_view(raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n"))
        cursor = 0
        for block in VAR_BLOCK.finditer(text):
            require(not text[cursor:block.start()].strip(), path, "Unsupported IEC local variable block")
            line = text.count("\n", 0, block.start(1)) + 1
            yield from declared_members(block[1], line, "IEC local variable")
            cursor = block.end()
        require(not text[cursor:].strip(), path, "Unsupported or unclosed IEC local variable block")
    except ValueError as error:
        raise BindingProblem(path, str(error)) from error


def saved_task_declaration(source, cpu, task, variable):
    software = cpu.with_suffix(".sw")
    program, task_class = task_program(source, software, task)
    root, _ = source.xml(program, "Program")
    require(root is not None, program, "Could not read the selected IEC program descriptor")
    paths = []
    for container in children(root, "Files"):
        for element in children(container, "File"):
            name = (element.text or "").strip()
            if Path(name).suffix.casefold() == ".var":
                path = source.member(program, name)
                require(path is not None and path not in paths, program, "Invalid or duplicate local declaration file")
                paths.append(path)
    path, row = one(((path, row) for path in paths for row in read_local_variables(source, path)
                     if row["name"].casefold() == variable.casefold()), program, f"local declaration for {variable!r}")
    expected = IEC_NODE_TYPES.get(row["type"].upper())
    require(expected is not None, path, f"Expected a directly declared numeric IEC scalar for {variable!r}")
    return {"taskClass": task_class, "taskAssignment": software.as_posix(), "program": program.as_posix(),
            "path": path.as_posix(), "sha256": source.sources[path.as_posix()], **row, "expectedDataType": expected}


@dataclass(frozen=True)
class BindingTarget:
    visualization: str
    content: str
    widget: str
    identifier: str
    require_default: bool = False
    verify_iec_type: bool = False

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

    def task_reference(self):
        reference = TASK_VARIABLE.fullmatch(self.identifier)
        require(reference is not None, ".", "Select a V1 OPC UA task scalar in ::Task:Variable form")
        for value in (self.visualization, self.content, self.widget):
            require(re.fullmatch(r"[a-zA-Z0-9_-]+", value), ".", f"Unsupported resource ID {value!r}")
        return reference.groups()

    def check_saved(self, source, cpu, saved, task, variable):
        extensions = {"vis", "binding", "uaserver", "uad"}
        if self.require_default:
            extensions.add("mappviewcfg")
        paths = package_files(source, cpu, "Cpu", extensions)
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
        if self.verify_iec_type:
            saved["iecDeclaration"] = saved_task_declaration(source, cpu, task, variable)
        if self.require_default:
            config, root = one(saved_documents(source, paths, ".mappviewcfg", "Configuration"),
                               cpu, "saved mapp View configuration")
            self.check_default(root, config)
            saved.update(defaultVisualization=self.visualization, serverConfig=config.as_posix())

    def check(self, archive, source, manifest, report):
        try:
            task, variable = self.task_reference()
            cpu = Path("Physical", manifest["configuration"], manifest["cpu"], "Cpu.pkg")
            saved, compiled = report["saved"], report["compiled"]
            report["state"] = "checking_saved"
            self.check_saved(source, cpu, saved, task, variable)

            report["state"] = "checking_compiled"
            self.compiled_binding(archive, compiled)
            if self.require_default:
                server_config = f"{SERVER}/{Path(saved['serverConfig']).name}"
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
            require(class_name == saved["widgetType"].replace(".", "/"), html_path, "Compiled widget type differs")
            compiled.update(content=html_path, widgetType=class_name)
            conn_path = f"{DATA}/OpcUaSrv.br"
            try:
                conn_root = open_configuration(packaged_bytes(archive, conn_path))
            except (ET.ParseError, LookupError, ValueError) as error:
                raise BindingProblem(conn_path, f"Could not read packaged OPC UA configuration: {error}") from error
            compiled.update(connection=conn_path, endpoint=connection(conn_root, conn_path))
            require(compiled["endpoint"] == saved["endpoint"], conn_path, "Compiled default OPC UA endpoint differs")
            map_member = f"{NODESETS}/{Path(saved['nodeMap']).stem}.uar.gz"
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
            if self.verify_iec_type:
                declaration = saved["iecDeclaration"]
                require(compiled["dataType"] == declaration["expectedDataType"], map_member,
                        f"Compiled node type {compiled['dataType']} differs from saved IEC {declaration['type']} "
                        f"(expected {declaration['expectedDataType']})")
            report["state"] = "matched"
        except BindingProblem as error:
            diagnostics = archive.diagnostics if report["state"] == "checking_compiled" else source
            diagnostics.issue("error", "binding_readback_mismatch", error.path, str(error))


def check_saved_cpu_endpoint(source, project, cpu, saved, review):
    path = cpu.parent.parent / "Hardware.hw"
    settings = {"path": path.as_posix(), "enabled": None, "port": None}
    saved["cpuOpcUa"] = settings
    hardware, _ = source.xml(path, "Hardware", optional=True)
    if hardware is None:
        source.issue("warning", "saved_binding_endpoint_unverified", path, "Could not read the saved CPU OPC UA settings")
        return
    module = selected_cpu_module(hardware, project["selectedCpu"], project["cpu"].get("moduleId"))
    if module is None:
        source.issue("warning", "saved_binding_endpoint_unverified", path, "Could not identify the selected CPU module")
        return

    flag = single_value(parameter_values(module, "ActivateOpcUa"))
    port = unsigned_value(single_value(parameter_values(module, "OpcUaPort")))
    settings["enabled"] = {"0": False, "1": True}.get(flag)
    settings["port"] = port if port is not None and 1 <= port <= 65535 else None
    if settings["enabled"] is False:
        review["state"] = "mismatched"
        source.issue("error", "saved_binding_endpoint_mismatch", path, "The selected CPU has its OPC UA server disabled")
    elif settings["enabled"] is None or settings["port"] is None:
        source.issue("warning", "saved_binding_endpoint_unverified", path,
                     "Could not verify explicit ActivateOpcUa and OpcUaPort values on the selected CPU")
    elif settings["port"] != saved["endpoint"]["port"]:
        review["state"] = "mismatched"
        source.issue("error", "saved_binding_endpoint_mismatch", path,
                     f"The CPU OPC UA port is {settings['port']} and mapp View uses {saved['endpoint']['port']}")
    else:
        review["state"] = "matched"


def check_saved_binding_placement(source, target, review):
    from .start_page import saved_content_placement

    path = Path(review["saved"]["visualization"])
    visualization, _ = source.xml(path, "Visualization")
    try:
        placement = saved_content_placement(source, path, visualization, target.visualization, target.content)
    except BindingProblem as error:
        review["startPagePlacement"]["state"] = "mismatched"
        review["state"] = "mismatched"
        source.issue("error", "saved_binding_start_page_mismatch", error.path, str(error))
    else:
        review["startPagePlacement"].update(state="matched", **placement)


def review_saved_binding(path, configuration, cpu, target, *, require_start_page=False):
    if require_start_page:
        target = replace(target, require_default=True)
    source = SavedFiles(path.parent)
    result = inspect_saved(source, Path(path.name), configuration, cpu)
    review = target.receipt()
    review.pop("compiled")
    review["state"] = "unverified"
    if require_start_page:
        review["startPagePlacement"] = {"state": "unverified"}
    result["savedBindingReview"] = review
    if result["selectedCpu"]:
        selected = Path("Physical", result["selectedConfiguration"], result["selectedCpu"], "Cpu.pkg")
        try:
            target.check_saved(source, selected, review["saved"], *target.task_reference())
        except BindingProblem as error:
            review["state"] = "mismatched"
            source.issue("error", "saved_binding_mismatch", error.path, str(error))
        else:
            check_saved_cpu_endpoint(source, result, selected, review["saved"], review)
            if require_start_page:
                check_saved_binding_placement(source, target, review)
    result["snapshot"] = source.snapshot()
    return result
