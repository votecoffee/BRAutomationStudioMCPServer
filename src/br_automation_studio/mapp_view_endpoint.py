"""Trace the saved AS4 mapp View startup URL and resources without opening a connection."""

from ipaddress import AddressValueError, IPv4Address
from pathlib import Path

from .binding_review import BindingProblem, VISUALIZATION, default_visualizations, one, require, saved_documents
from .hardware_timing import catalog_ports, exact_cpu_catalog, parameter_values, selected_cpu_module, single_value, unsigned_value
from .project import children, package_files
from .start_page import PAGE, resource_id, saved_resource, visualization_start


PROTOCOLS = {"1": ("http",), "2": ("https",), "3": ("http", "https")}
PORT_FIELDS = {"http": "WebServerPort", "https": "WebServerPortSsl"}


def static_address(saved, studio, project, cpu_catalog, result, hardware):
    connector = result["connector"]
    configuration, _ = saved.xml(hardware, "Hardware")
    require(configuration is not None, hardware, "Could not read the selected hardware configuration")
    cpu = selected_cpu_module(configuration, project["selectedCpu"], project["cpu"].get("moduleId"))
    require(cpu is not None, hardware, "Could not identify one top-level hardware module for the selected CPU")
    catalog = exact_cpu_catalog(cpu, cpu_catalog)
    require(catalog is not None, hardware, "Could not verify the exact saved CPU hardware revision")
    result["cpuCatalog"] = catalog.as_posix()
    definition, _ = studio.xml(catalog, "Module")
    require(catalog_ports(definition, "Ethernet").count(connector) == 1
            and connector not in catalog_ports(definition, "Powerlink"),
            hardware, f"{connector!r} is outside the dedicated Ethernet connector scope")
    port = one((node for node in children(cpu, "Connector") if node.get("Name") == connector),
               hardware, f"saved Ethernet connector {connector!r}")
    value = single_value(parameter_values(port, "Mode"))
    result["addressMode"] = value
    require(single_value(parameter_values(port, "ActivateDevice")) == "1", hardware,
            f"{connector} needs one explicit enabled Ethernet setting")
    require(value == "Manual", hardware, f"{connector} needs a saved manual IPv4 mode to derive a static URL")
    raw = single_value(parameter_values(port, "InternetAddress"))
    try:
        address = IPv4Address(raw or "")
    except AddressValueError as error:
        raise BindingProblem(hardware, f"{connector} needs one valid saved InternetAddress") from error
    require(not (address.is_unspecified or address.is_loopback or address.is_multicast
                 or int(address) == 0xFFFFFFFF), hardware, f"{connector} needs a usable physical IPv4 address")
    return str(address)


def saved_startup(saved, cpu, result):
    paths = package_files(saved, cpu, "Cpu", {"mappviewcfg", "vis"})
    server_path, config = one(saved_documents(saved, paths, ".mappviewcfg", "Configuration"), cpu,
                              "registered mapp View server configuration")
    result["serverConfig"] = server_path.as_posix()
    element = one((node for node in children(config, "Element") if node.get("Type") == "MAPPVIEWCFG"),
                  server_path, "MAPPVIEWCFG element")
    server = one((node for node in children(element, "Group") if node.get("ID") == "Server"),
                 server_path, "mapp View Server group")
    protocol = one((node for node in children(server, "Selector") if node.get("ID") == "WebServerProtocol"),
                   server_path, "WebServerProtocol selector")
    schemes = PROTOCOLS.get(protocol.get("Value"))
    require(schemes is not None, server_path, "Unsupported saved mapp View protocol selector")

    properties = children(protocol, "Property")
    listeners = []
    for scheme in schemes:
        field = PORT_FIELDS[scheme]
        raw = one((node for node in properties if node.get("ID") == field), server_path, field).get("Value")
        port = unsigned_value(raw)
        require(port is not None and 1 <= port <= 65535, server_path, f"{field} needs a valid explicit TCP port")
        listeners.append({"scheme": scheme, "port": port, "defaultUrl": None, "explicitVisualizationUrl": None})
    require(len({row["port"] for row in listeners}) == len(listeners), server_path,
            "HTTP and HTTPS cannot use the same configured port")
    result["listeners"] = listeners

    visualization = resource_id(one(default_visualizations(config), server_path, "default visualization"),
                                server_path, "default visualization ID")
    result["visualizationId"] = visualization
    vis_path, visu = saved_resource(saved, paths, ".vis", VISUALIZATION, visualization, server_path)
    result["visualization"] = vis_path.as_posix()
    page = visualization_start(visu, vis_path, visualization)
    result["startPageId"] = page
    logical = package_files(saved, Path("Logical/Package.pkg"), "Package", {"page"})
    page_path, _ = saved_resource(saved, logical, ".page", PAGE, page, vis_path)
    result["startPage"] = page_path.as_posix()

    result["sslConfiguration"] = single_value([node.get("Value") for node in properties
                                               if node.get("ID") == "SSLConfiguration"]) if "https" in schemes else None
    for row in listeners:
        if row["scheme"] == "https" and not result["sslConfiguration"]:
            saved.issue("warning", "mapp_view_entry_unverified", server_path,
                        "The saved HTTPS listener needs one explicit SSLConfiguration reference")
            continue
        if address := result["address"]:
            url = f"{row['scheme']}://{address}:{row['port']}/index.html"
            row["defaultUrl"] = url
            row["explicitVisualizationUrl"] = f"{url}?visuId={visualization}"


def review_mapp_view_endpoint(saved, studio, project, cpu_catalog, connector):
    result = {"state": "unverified", "connector": connector, "hardware": None, "cpuCatalog": None,
              "address": None, "addressMode": None, "serverConfig": None, "listeners": [],
              "sslConfiguration": None, "visualizationId": None, "visualization": None,
              "startPageId": None, "startPage": None}
    if not project["selectedCpu"]:
        return result

    configuration = Path("Physical", project["selectedConfiguration"])
    hardware = configuration / "Hardware.hw"
    result["hardware"] = hardware.as_posix()
    before = len(saved.diagnostics)
    try:
        result["address"] = static_address(saved, studio, project, cpu_catalog, result, hardware)
    except BindingProblem as error:
        saved.issue("warning", "mapp_view_entry_unverified", error.path, str(error))
    try:
        saved_startup(saved, configuration / project["selectedCpu"] / "Cpu.pkg", result)
    except BindingProblem as error:
        saved.issue("warning", "mapp_view_entry_unverified", error.path, str(error))
    if len(saved.diagnostics) == before:
        result["state"] = "matched"
    return result
