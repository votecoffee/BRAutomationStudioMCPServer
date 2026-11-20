
"""Materialize a bound PC simulation RUC and verify its mapp View assets over loopback."""

from hashlib import sha256
from http.client import HTTPConnection, HTTPException
from ipaddress import ip_address
from pathlib import Path
from shutil import copyfile
import time
from urllib.parse import quote
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .native_build import fingerprint, utc_now, write_json
from .project import ProjectXml, SavedFiles, XML_LIMIT, children, package_files, tag
from .ruc_review import Archive, CONTENT, DATA, VIEW, WIDGET_SCRIPT, bound_receipts, open_configuration, review_ruc
from .windows_job import observed_process, run_build
from .windows_network import endpoints
from .windows_version import product_version

HOST = "127.0.0.1"
# Effective engineering endpoints observed in the supported AS4 ARsim profile.
ENGINEERING_PORTS = {11160, 11169}


def selected(nodes, name, value):
    return [node for node in nodes if node.get(name) == value]


def only(nodes, label):
    if len(nodes) != 1:
        raise ValueError(f"Expected exactly one {label}")
    return nodes[0]


def setting(node, tag_name, key):
    return only(selected(children(node, tag_name), "ID", key), key).get("Value")


def user_port(value, name):
    if not value or not value.isascii() or not value.isdecimal() or len(value) > 5:
        raise ValueError(f"{name} needs an explicit numeric port")
    port = int(value)
    if not 1024 <= port <= 65535:
        raise ValueError(f"{name} must use an unprivileged port")
    return port


def loopback_profile(project, manifest):
    source = SavedFiles(project.parent)
    configuration = Path("Physical", manifest["configuration"])
    cpu = configuration / manifest["cpu"]
    hardware, _ = source.xml(configuration / "Hardware.hw", "Hardware")
    if hardware is None:
        raise ValueError("Could not read the selected Hardware.hw")
    module = only(children(hardware, "Module"), "hardware CPU")
    if module.get("Type") != "PC_any" or module.get("Name") != cpu.name:
        raise ValueError("ARsim smoke requires the saved PC_any CPU module")
    connector = only(selected(children(module, "Connector"), "Name", "IF3"), "IF3 connector")
    if (setting(module, "Parameter", "SimulationIPAddress") != HOST
            or setting(connector, "Parameter", "InternetAddress") != HOST):
        raise ValueError("Set both saved PC_any IP addresses explicitly to 127.0.0.1")
    ina_port = user_port(setting(module, "Parameter", "SimulationInaPort"), "SimulationInaPort")

    definitions = package_files(source, cpu / "Cpu.pkg", "Cpu", {"mappviewcfg", "uaserver"})
    web_path = only([path for path in definitions if path.suffix.lower() == ".mappviewcfg"],
                    "mapp View server configuration")
    web, _ = source.xml(web_path, "Configuration")
    if web is None:
        raise ValueError("Could not read the mapp View server configuration")
    element = only(selected(children(web, "Element"), "Type", "MAPPVIEWCFG"), "MAPPVIEWCFG element")
    server = only(selected(children(element, "Group"), "ID", "Server"), "mapp View Server group")
    protocol = only(selected(children(server, "Selector"), "ID", "WebServerProtocol"), "WebServerProtocol selector")
    if protocol.get("Value") != "1":
        raise ValueError("Loopback smoke supports the saved AS4 HTTP listener configuration")
    http_port = user_port(setting(protocol, "Property", "WebServerPort"), "WebServerPort")

    opcua = setting(module, "Parameter", "ActivateOpcUa")
    if opcua not in {"0", "1"}:
        raise ValueError("The saved OPC UA activation state needs to be explicit")
    ua_port = user_port(setting(module, "Parameter", "OpcUaPort"), "OpcUaPort") if opcua == "1" else None
    ua_paths = [path for path in definitions if path.suffix.lower() == ".uaserver"]
    if ua_port and not ua_paths:
        raise ValueError("The saved mapp View project needs an explicit local OPC UA connection")
    for ua_path in ua_paths:
        ua, _ = source.xml(ua_path, "Configuration")
        if ua is None:
            raise ValueError("Could not read the mapp View OPC UA configuration")
        check_ua_settings(ua, ua_port)
    if source.diagnostics:
        raise ValueError(f"Could not verify the saved runtime inputs: {source.diagnostics[0]['message']}")
    ports = [port for port in (http_port, ina_port, ua_port) if port is not None]
    if len(set(ports)) != len(ports) or set(ports) & ENGINEERING_PORTS:
        raise ValueError("Use distinct configured ports outside the observed engineering defaults")

    snapshot = source.snapshot()
    pinned = {Path(row["path"]): row["sha256"] for row in manifest["inputs"]["files"]}
    if any(pinned.get(Path(row["path"])) != row["sha256"] for row in snapshot["files"]):
        raise ValueError("The runtime configuration changed after the build plan was generated")
    return {"httpPort": http_port, "opcUaPort": ua_port, "simulationInaPort": ina_port,
            "guardedPorts": sorted(set(ports) | ENGINEERING_PORTS), "snapshot": snapshot}


def check_ua_settings(root, port):
    connections = 0
    for group in root.iter():
        if tag(group) != "Group":
            continue
        fields = {item.get("ID"): item.get("Value") for item in children(group, "Property")}
        if not {"IPAddress", "Hostname", "EndpointUrl", "Port"} & fields.keys():
            continue
        connections += 1
        if fields.get("IPAddress") != HOST or fields.get("Hostname") or fields.get("EndpointUrl"):
            raise ValueError("HMI OPC UA connections must use an explicit loopback IP")
        if user_port(fields.get("Port"), "OPC UA connection Port") != port:
            raise ValueError("HMI OPC UA connection differs from the declared runtime port")
    if not connections:
        raise ValueError("Could not verify an explicit HMI OPC UA connection")


def expected_assets(archive_path, review, profile):
    names = {f"{VIEW}/index.html"}
    for widget in review["widgets"]:
        names.add(f"{CONTENT}/{widget['contentId']}.html")
    if review["widgets"]:
        names.add(WIDGET_SCRIPT)
    source = SavedFiles(archive_path.parent)
    with ZipFile(archive_path) as package:
        archive = Archive(package, source)
        info = archive.member("ProjectInformation.xml")
        if info is None:
            raise ValueError("Missing RUC ProjectInformation.xml")
        xml = ET.fromstring(archive.read(info), parser=ET.XMLParser(target=ProjectXml()))
        if xml.findtext("CPUType") != "AR000" or xml.findtext("RuntimeType") != "AR Simulation":
            raise ValueError("RUC is not identified as an AR000 simulation package")
        if profile["opcUaPort"] is not None:
            configuration = archive.member(f"{DATA}/OpcUaSrv.br")
            if configuration is None:
                raise ValueError("Compiled OPC UA connection module is missing or ambiguous")
            check_ua_settings(open_configuration(archive.read(configuration)), profile["opcUaPort"])
        assets = []
        for name in sorted(names):
            item = archive.member(name)
            if item is None:
                raise ValueError(f"Missing or ambiguous package asset {name}")
            data = archive.read(item)
            assets.append({"path": name.removeprefix(VIEW), "bytes": len(data), "sha256": sha256(data).hexdigest()})
        return xml.findtext("ARVersion"), assets


def check_free(ports):
    occupied = [row for row in endpoints() if row["port"] in ports]
    if occupied:
        raise ValueError(f"ARsim ports are already in use: {occupied}")


def check_owned(ports, pids):
    owned = []
    for row in endpoints():
        if row["pid"] in pids:
            if not ip_address(row["address"]).is_loopback:
                raise ValueError(f"ARsim exposed a non-loopback endpoint: {row}")
            owned.append(row)
        elif row["port"] in ports:
            raise ValueError(f"ARsim guard port belongs to another process: {row}")
    return sorted(owned, key=lambda item: (item["protocol"], item["port"], item["address"], item["pid"]))


def http_asset(port, asset):
    connection = HTTPConnection(HOST, port, timeout=2)
    try:
        connection.request("GET", quote(asset["path"], safe="/"), headers={"Accept-Encoding": "identity"})
        response = connection.getresponse()
        body = response.read(XML_LIMIT + 1)
        return {"path": asset["path"], "status": response.status,
                "bytes": len(body), "sha256": sha256(body).hexdigest()}
    finally:
        connection.close()


def probe_http(session, profile, assets, receipt, timeout):
    port = profile["httpPort"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.worker.poll() is not None:
            raise ValueError(f"ARsim loader exited with code {session.worker.returncode}")
        owned = check_owned(profile["guardedPorts"], session.job.pids())
        if any(row["protocol"] == "tcp" and row["port"] == port and row["address"] == HOST for row in owned):
            try:
                first = http_asset(port, next(item for item in assets if item["path"] == "/index.html"))
                if first["status"] == 200:
                    break
            except (OSError, HTTPException):
                pass
        time.sleep(0.25)
    else:
        raise ValueError("ARsim did not serve the expected index within the startup window")

    for want in assets:
        check_owned(profile["guardedPorts"], session.job.pids())
        response = http_asset(port, want)
        receipt["assets"].append(response)
        if response["status"] != 200 or any(response[key] != want[key] for key in ("bytes", "sha256")):
            raise ValueError(f"ARsim response did not match the packaged asset: {want['path']}")
    receipt["ownedEndpoints"] = check_owned(profile["guardedPorts"], session.job.pids())


def ascii_pil_path(path):
    value = str(path)
    if any(ord(char) < 32 or ord(char) > 126 or char == '"' for char in value):
        raise ValueError("PVITransfer staging paths must be printable ASCII without quotation marks")
    return value


def arsim_smoke(project, job_dir, pvi, session_dir, timeout=30, browser_probe=None):
    report = review_ruc(project, job_dir)
    smoke = {"state": "not_started", "packageSha256": None, "assets": [], "cleanup": None}
    report["arsimSmoke"] = smoke
    if any(item["severity"] == "error" for item in report["diagnostics"]):
        return report

    def fail(code, message):
        report["diagnostics"].append({"severity": "error", "scope": "arsim_smoke",
                                      "code": code, "path": ".", "message": message})

    observed = None
    receipt = None
    try:
        plan, job, manifest, archive_path = bound_receipts(SavedFiles(job_dir))
        reviewed = report["rucReview"]
        if plan["sha256"] != reviewed["planSha256"] or job["package"]["sha256"] != reviewed["archive"]["sha256"]:
            raise ValueError("The completed job evidence changed after RUC review")
        profile = loopback_profile(project, manifest)
        if browser_probe is not None:
            smoke["browser"] = {"state": "not_started"}
            probe_browser = browser_probe.prepare(reviewed, smoke["browser"])
        pvi = pvi.resolve()
        pvi_version = product_version(pvi)
        if pvi.name.casefold() != "pvitransfer.exe" or not (pvi_version or "").startswith("PVI4."):
            raise ValueError("Select an installed PVI 4 PVITransfer.exe")
        session_dir = session_dir.resolve()
        if any(session_dir.is_relative_to(root.resolve()) for root in (project.parent, job_dir, pvi.parent)):
            raise ValueError("Select a new session directory outside all input trees")
        target = session_dir / "runtime"
        staged = session_dir / "input.zip"
        instructions = session_dir / "create.pil"
        pvi_log = session_dir / "pvi.log"
        instruction = f'CreateARsimStructure "{ascii_pil_path(staged)}", "{ascii_pil_path(target)}", "Start=0"\n'
        check_free(profile["guardedPorts"])

        session_dir.mkdir()
        receipt = session_dir / "session.json"
        smoke.update(state="materializing", directory=str(session_dir), startedAt=utc_now(),
                     packageSha256=job["package"]["sha256"], planSha256=plan["sha256"],
                     pvi={"path": str(pvi), "version": pvi_version, "sha256": fingerprint(pvi)}, profile=profile)
        write_json(receipt, smoke)
        copyfile(archive_path, staged)
        if fingerprint(staged) != smoke["packageSha256"]:
            raise ValueError("The staged RUC differs from the completed build")
        version, assets = expected_assets(staged, reviewed, profile)
        smoke["runtimeVersion"] = version
        instructions.write_text(instruction, encoding="ascii", newline="\n")
        with (session_dir / "pvi-stdout.log").open("xb") as log:
            code, timed_out, cleanup = run_build([str(pvi), "-silent", f"-{instructions}", f"-{pvi_log}"],
                                                 session_dir, lambda pid: None, log, 75)
        smoke["pvi"].update(exitCode=code, timedOut=timed_out, cleanup=cleanup)
        if code or timed_out or cleanup != "empty" or b"PROCESS FINISHED (SUCCESS)" not in pvi_log.read_bytes():
            raise ValueError("PVITransfer did not confirm completion of runtime materialization")
        loader = target / "ar000loader.exe"
        if not loader.is_file() or not loader.resolve().is_relative_to(target.resolve()):
            raise ValueError("The materialized runtime does not contain its expected loader")

        check_free(profile["guardedPorts"])
        smoke.update(state="probing", loaderSha256=fingerprint(loader))
        write_json(receipt, smoke)
        with (session_dir / "arsim-stdout.log").open("xb") as log:
            with observed_process([str(loader)], target, log) as observed:
                probe_http(observed, profile, assets, smoke, timeout)
                if browser_probe is not None:
                    http_port = profile["httpPort"]

                    def guard_runtime():
                        if observed.worker.poll() is not None:
                            raise ValueError("ARsim exited during the browser smoke")
                        owned = check_owned(profile["guardedPorts"], observed.job.pids())
                        if not any(row["protocol"] == "tcp" and row["port"] == http_port
                                   and row["address"] == HOST for row in owned):
                            raise ValueError("ARsim lost its owned HTTP listener during the browser smoke")

                    probe_browser(session_dir, http_port, guard_runtime)
        smoke["cleanup"] = observed.cleanup
        check_free(profile["guardedPorts"])
        if observed.cleanup != "empty":
            raise ValueError("ARsim process cleanup could not be verified")
        smoke["state"] = "complete"
        report["evidence"] = "arsim_loopback_browser" if browser_probe is not None else "arsim_loopback_http"
    except (BadZipFile, ET.ParseError, HTTPException, OSError, RuntimeError, ValueError) as error:
        smoke["state"] = "failed"
        if observed is not None:
            smoke["cleanup"] = observed.cleanup
        fail("arsim_smoke_failed", str(error))
    except KeyboardInterrupt:
        smoke["state"] = "interrupted"
        if observed is not None:
            smoke["cleanup"] = observed.cleanup
        fail("arsim_smoke_interrupted", "Runtime smoke was interrupted")
    finally:
        if receipt is not None:
            smoke["finishedAt"] = utc_now()
            write_json(receipt, smoke)
    return report
