"""Verify mapp View rendering and fresh counter samples in a private headless Chrome session."""

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
from math import isfinite
from pathlib import Path
import json
import re
from tempfile import TemporaryDirectory
import time
from typing import ClassVar
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .arsim_smoke import HOST, loopback_get
from .native_build import fingerprint
from .project import ProjectXml, XML_LIMIT
from .windows_job import observed_process
from .windows_network import endpoints
from .windows_version import product_version

ASSEMBLY_WIDGET = "widgets/mapp6D/Assembly6D"
NUMBER = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
PAGE_SOCKET = re.compile(r"/devtools/page/[a-zA-Z0-9_-]+")
LINK_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SVG_ROOT = "{http://www.w3.org/2000/svg}svg"
TRANSIENT_CONTEXT_ERRORS = {"Cannot find default execution context", "Execution context was destroyed."}


def assembly_dom_id(widgets, selected, counter):
    available = [f"{item['contentId']}_{item['id']}" for item in widgets]
    if selected is None and len(available) == 1:
        selected = available[0]
    if selected is None or available.count(selected) != 1:
        raise ValueError("Select the unique browser DOM ID of a reviewed Assembly6D widget")
    if selected == counter:
        raise ValueError("Select a separate numeric counter widget")
    return selected


def layout_asset_paths(widgets, selected):
    widget = next(item for item in widgets if f"{item['contentId']}_{item['id']}" == selected)
    paths = {}
    for role, key in (("assembly", "assembly"), ("trafficManager", "manager")):
        link = widget.get(key)
        if role == "trafficManager" and link in (None, ""):
            continue
        if not isinstance(link, str) or not LINK_NAME.fullmatch(name := link.removeprefix("mapp6D::")):
            return {}
        paths[role] = f"/FileDevice:6DData/{name}.svg"
    return paths


def layout_observation(value, paths):
    value = value if isinstance(value, dict) else {}
    count = value.get("boundedSvgCount")
    count = count if type(count) is int and count >= 0 else None
    raw_statuses = value.get("statuses")
    observed = raw_statuses if isinstance(raw_statuses, dict) else {}
    statuses = {role: status if type(status := observed.get(role)) is int and 100 <= status <= 599 else None
                for role in paths}
    if any(status is not None and status >= 400 for status in statuses.values()):
        state = "request_failed"
    elif paths and count and all(status in (200, 304) for status in statuses.values()):
        state = "observed"
    else:
        state = "unverified"
    return {"state": state, "boundedSvgCount": count,
            "assets": {role: {"path": path, "httpStatus": statuses[role]} for role, path in paths.items()}}


def seconds_left(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("The browser smoke window elapsed")
    return min(2, remaining)


def debug_listener(browser, port):
    if browser.worker.poll() is not None:
        raise ValueError(f"Headless Chrome exited with code {browser.worker.returncode}")
    pids = browser.job.pids()
    matching = [row for row in endpoints() if row["protocol"] == "tcp" and row["port"] == port]
    if any(row["address"] != HOST or row["pid"] not in pids for row in matching):
        raise ValueError("Chrome DevTools port is not exclusively owned on IPv4 loopback")
    return matching[0] if matching else None


def debugger_target(port, deadline):
    connection = HTTPConnection(HOST, port, timeout=seconds_left(deadline))
    try:
        connection.request("GET", "/json/list")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"Unexpected Chrome DevTools target status {response.status}")
        data = response.read(65537)
    finally:
        connection.close()
    if len(data) > 65536:
        raise ValueError("Chrome DevTools target list exceeds the receipt limit")
    targets = json.loads(data)
    if not isinstance(targets, list) or any(not isinstance(item, dict) for item in targets):
        raise ValueError("Unexpected Chrome DevTools target list")
    pages = [item for item in targets if item.get("type") == "page" and item.get("url") == "about:blank"]
    if not pages:
        return None
    if len(pages) != 1 or not isinstance(address := pages[0].get("webSocketDebuggerUrl"), str):
        raise ValueError("Could not select one private Chrome page")
    url = urlsplit(address)
    if (url.scheme != "ws" or url.netloc != f"{HOST}:{port}" or url.query or url.fragment
            or not PAGE_SOCKET.fullmatch(url.path)):
        raise ValueError("Chrome advertised a debugger outside its owned page endpoint")
    return f"ws://{HOST}:{port}{url.path}"


def wait_for_debugger(browser, path, guard_runtime, deadline):
    while time.monotonic() < deadline:
        guard_runtime()
        if browser.worker.poll() is not None:
            raise ValueError(f"Headless Chrome exited with code {browser.worker.returncode}")
        try:
            with path.open("rb") as file:
                lines = file.read(1024).splitlines()
        except FileNotFoundError:
            lines = []
        if len(lines) >= 2 and re.fullmatch(rb"[0-9]{1,5}", lines[0]):
            port = int(lines[0])
            if not 1024 <= port <= 65535:
                raise ValueError("Chrome advertised an invalid DevTools port")
            if listener := debug_listener(browser, port):
                try:
                    address = debugger_target(port, deadline)
                except (OSError, HTTPException):
                    address = None
                if address:
                    return address, listener
        time.sleep(0.2)
    raise TimeoutError("Headless Chrome did not expose a private page within the browser window")


class Page:
    def __init__(self, frames, guard, deadline):
        self.frames = frames
        self.guard = guard
        self.deadline = deadline
        self.request_id = 0

    def request(self, method, params):
        self.guard()
        self.request_id += 1
        self.frames.settimeout(seconds_left(self.deadline))
        self.frames.send(json.dumps({"id": self.request_id, "method": method, "params": params}))
        while True:
            self.frames.settimeout(seconds_left(self.deadline))
            reply = json.loads(self.frames.recv())
            if reply.get("id") == self.request_id:
                return reply

    def navigate(self, url):
        reply = self.request("Page.navigate", {"url": url})
        if "error" in reply or not reply.get("result", {}).get("loaderId") or reply["result"].get("errorText"):
            raise ValueError("Chrome could not load the selected document")

    def read(self, expression):
        reply = self.request("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if error := reply.get("error"):
            if error.get("message") in TRANSIENT_CONTEXT_ERRORS:
                return {}
            raise ValueError("Chrome could not evaluate the HMI document")
        result = reply.get("result", {})
        value = result.get("result", {}).get("value")
        if "exceptionDetails" in result or not isinstance(value, dict):
            raise ValueError("Chrome did not return HMI document evidence")
        return value


def widget_expression(assembly, counter, layout_paths=None, status_widgets=()):
    return f"""(() => {{
        const assembly = document.getElementById({json.dumps(assembly)});
        const read = (id) => {{
            const widget = document.getElementById(id);
            return widget?.getClientRects().length
                ? (widget.querySelector('input')?.value ?? widget.textContent)?.trim() : null;
        }};
        const paths = {json.dumps(layout_paths or {})};
        const layout = assembly && {{
            boundedSvgCount: Array.from(assembly.querySelectorAll('svg')).filter(
                svg => Array.from(svg.getClientRects()).some(rect => rect.width > 0 && rect.height > 0)
            ).length,
            statuses: Object.fromEntries(Object.entries(paths).map(([role, path]) => {{
                const entries = performance.getEntriesByName(new URL(path, location.href).href, 'resource');
                return [role, entries.at(-1)?.responseStatus ?? null];
            }}))
        }};
        return {{
            url: location.href,
            timeOrigin: performance.timeOrigin,
            widgetType: assembly?.getClientRects().length
                ? assembly.getAttribute('data-brease-widget') : null,
            value: read({json.dumps(counter)}),
            statusValues: Object.fromEntries({json.dumps(list(status_widgets))}.map(id => [id, read(id)])),
            layout
        }};
    }})()"""


def document_origin(state, url):
    origin = state.get("timeOrigin")
    if state.get("url") == url and type(origin) in {float, int} and isfinite(origin) and origin > 0:
        return origin
    return None


def numeric_text(value):
    return Decimal(value) if isinstance(value, str) and len(value) <= 64 and NUMBER.fullmatch(value) else None


def check_document_status(state, receipt, expected):
    raw = state.get("statusValues")
    raw = raw if isinstance(raw, dict) else {}
    seen = {widget: raw.get(widget) for widget in expected}
    if all(numeric_text(seen[widget]) == value for widget, value in expected.items()):
        receipt["statusValues"] = seen
        return True
    receipt["lastStatusValues"] = seen
    if "statusValues" in receipt:
        raise ValueError("A displayed status changed after the reviewed values appeared in the HMI document")
    return False


def collect_samples(page, expression, url, receipt, count, prior_origins=(), after=Decimal(0),
                    *, layout_paths=None, require_layout=False, check_assembly=True, expected_status=None):
    latest = after
    while time.monotonic() < page.deadline:
        result = page.read(expression)
        origin = document_origin(result, url)
        widget_matches = not check_assembly or result.get("widgetType") == ASSEMBLY_WIDGET
        if origin is not None and origin not in prior_origins and widget_matches:
            if "timeOrigin" in receipt and receipt["timeOrigin"] != origin:
                raise ValueError("The HMI document changed during counter sampling")
            receipt["timeOrigin"] = origin
            if check_assembly:
                receipt["widgetType"] = ASSEMBLY_WIDGET
            if layout_paths:
                receipt["layout"] = layout_observation(result.get("layout"), layout_paths)
            value = result.get("value")
            status_matches = not expected_status or check_document_status(result, receipt, expected_status)
            if status_matches and len(receipt["counterSamples"]) < count:
                numeric = numeric_text(value)
                if numeric is not None and numeric > latest:
                    latest = numeric
                    receipt["counterSamples"].append(value)
            if (status_matches and len(receipt["counterSamples"]) == count
                    and (not require_layout or receipt.get("layout", {}).get("state") == "observed")):
                return
        time.sleep(0.2)
    if require_layout and len(receipt["counterSamples"]) == count:
        raise TimeoutError("Successful 6DData SVG requests and an inline SVG with layout bounds were not verified")
    if expected_status:
        raise TimeoutError("The expected displayed status values and changing numeric counter were not verified")
    raise TimeoutError("The displayed Assembly6D and changing numeric counter were not verified")


def verify_navigation(page, url, receipt):
    paths = receipt.get("layoutPaths")
    required = receipt.get("requireLayoutAssets", False)
    expected = receipt.get("expectedStatus", {})
    assembly = receipt["assemblyWidgetId"]
    options = {"layout_paths": paths, "require_layout": required, "check_assembly": assembly is not None,
               "expected_status": expected}
    expression = widget_expression(assembly, receipt["counterWidgetId"], paths, expected)
    page.navigate(url)
    receipt["state"] = "first_document"
    first = receipt["initial"] = {"counterSamples": []}
    collect_samples(page, expression, url, first, 3, **options)

    receipt["state"] = "leaving_page"
    page.navigate("about:blank")
    while time.monotonic() < page.deadline:
        blank = document_origin(page.read(expression), "about:blank")
        if blank is not None and blank != first["timeOrigin"]:
            receipt["blankTimeOrigin"] = blank
            break
        time.sleep(0.2)
    else:
        raise TimeoutError("Chrome did not confirm that it left the HMI document")

    page.navigate(url)
    receipt["state"] = "reopened_document"
    reopened = receipt["reopened"] = {"counterSamples": []}
    collect_samples(page, expression, url, reopened, 2,
                    (first["timeOrigin"], blank), Decimal(first["counterSamples"][-1]), **options)


def capture_layout_assets(session_dir, http_port, paths, guard_runtime):
    bodies, assets = {}, {}
    for role, path in paths.items():
        guard_runtime()
        status, media_type, body = loopback_get(http_port, path)
        guard_runtime()
        if status != 200 or (media_type or "").partition(";")[0].strip().lower() != "image/svg+xml":
            raise ValueError(f"Layout capture expected HTTP 200 and image/svg+xml for {path}, "
                             f"received HTTP {status} and {media_type!r}")
        if len(body) > XML_LIMIT:
            raise ValueError(f"Layout SVG exceeds the capture limit for {path}")
        try:
            root = ET.fromstring(body, parser=ET.XMLParser(target=ProjectXml()))
        except (ET.ParseError, LookupError, ValueError) as error:
            raise ValueError(f"Invalid layout SVG at {path}: {error}") from error
        if root.tag != SVG_ROOT:
            raise ValueError(f"Expected a namespaced SVG at {path}")
        bodies[role] = body
        assets[role] = {"path": path, "status": status, "contentType": media_type,
                        "bytes": len(body), "sha256": sha256(body).hexdigest(),
                        "file": f"layout-assets/{role}.svg"}

    root = session_dir.resolve(strict=True)
    target = root / "layout-assets"
    if target.exists() or target.is_symlink():
        raise ValueError("The session already has a layout asset directory")
    with TemporaryDirectory(prefix=".layout-assets-", dir=root) as temporary:
        source = Path(temporary) / target.name
        source.mkdir()
        for role, body in bodies.items():
            with (source / f"{role}.svg").open("xb") as stream:
                stream.write(body)
        guard_runtime()
        if not all(path.resolve().is_relative_to(root) for path in (source, target)):
            raise ValueError("Layout capture paths must stay in the owned session")
        source.rename(target)
    return assets


@dataclass(frozen=True)
class ChromeProbe:
    executable: Path
    counter_widget: str
    assembly_widget: str | None
    timeout: int
    require_layout: bool = False
    capture_layout: bool = False
    expected_status: tuple[tuple[str, int], ...] = ()
    check_assembly: bool = True
    key: ClassVar[str] = "browser"
    evidence: ClassVar[str] = "arsim_loopback_browser"

    def prepare(self, reviewed, profile, receipt):
        try:
            from websocket import WebSocketException, create_connection
        except ImportError as error:
            raise ValueError("Install the br-automation-studio-tools browser extra in this environment") from error
        chrome = self.executable.resolve()
        version = product_version(chrome)
        if chrome.name.lower() != "chrome.exe" or not version:
            raise ValueError("Select an installed Google Chrome chrome.exe")
        widget = assembly_dom_id(reviewed["widgets"], self.assembly_widget, self.counter_widget) if self.check_assembly else None
        paths = layout_asset_paths(reviewed["widgets"], widget) if widget is not None else {}
        require_layout = self.require_layout or self.capture_layout
        if require_layout and not paths:
            raise ValueError("The reviewed widget has no supported 6DData SVG paths")
        receipt.update(state="ready", assemblyWidgetId=widget, layoutPaths=paths, requireLayoutAssets=require_layout,
                       counterWidgetId=self.counter_widget,
                       chrome={"path": str(chrome), "version": version, "sha256": fingerprint(chrome)})
        if self.expected_status:
            receipt["expectedStatus"] = dict(self.expected_status)
        if self.capture_layout:
            receipt["layoutCapture"] = {"state": "not_started", "assets": {}}

        http_port = profile["httpPort"]

        def probe(session_dir, owned_listener):
            listener = owned_listener(http_port)
            if listener is None:
                raise ValueError("ARsim lost its owned HTTP listener before the browser smoke")

            def guard_runtime():
                if owned_listener(http_port) != listener:
                    raise ValueError("ARsim lost its original HTTP listener during the browser smoke")

            try:
                self.observe(chrome, session_dir, http_port, guard_runtime, receipt, create_connection)
            except WebSocketException as error:
                raise ValueError(f"Chrome DevTools transport failed: {error}") from error
            if self.capture_layout:
                receipt["state"] = "capturing_layout"
                capture = receipt["layoutCapture"]
                capture.update(state="fetching", ownedListener=listener)
                try:
                    assets = capture_layout_assets(session_dir, http_port, paths, guard_runtime)
                except (HTTPException, OSError, RuntimeError, ValueError):
                    capture["state"] = "failed"
                    raise
                capture.update(state="complete", assets=assets)
                receipt["state"] = "complete"

        return probe

    def observe(self, chrome, session_dir, http_port, guard_runtime, receipt, connect):
        url = f"http://{HOST}:{http_port}/"
        profile = session_dir / "chrome-profile"
        profile.mkdir()
        command = [str(chrome), "--headless=new", "--remote-debugging-port=0",
                   f"--user-data-dir={profile}", "--disable-background-networking", "--disable-quic",
                   "--no-proxy-server", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                   "--no-first-run", "--no-default-browser-check", "about:blank"]
        receipt.update(state="starting_browser", url=url, cleanup=None)
        browser = None
        try:
            with (session_dir / "chrome-stdout.log").open("xb") as log:
                with observed_process(command, session_dir, log) as browser:
                    deadline = time.monotonic() + self.timeout
                    address, listener = wait_for_debugger(browser, profile / "DevToolsActivePort",
                                                          guard_runtime, deadline)
                    receipt["debuggerListener"] = listener

                    def guard():
                        guard_runtime()
                        if debug_listener(browser, listener["port"]) != listener:
                            raise ValueError("Chrome DevTools lost its original listener")

                    guard()
                    frames = connect(address, timeout=seconds_left(deadline), suppress_origin=True,
                                     http_no_proxy=[HOST], redirect_limit=0)
                    try:
                        if frames.getstatus() != 101:
                            raise ValueError("Chrome DevTools did not upgrade the owned connection")
                        verify_navigation(Page(frames, guard, deadline), url, receipt)
                        guard()
                    finally:
                        frames.shutdown()
        finally:
            if browser is not None:
                receipt["cleanup"] = browser.cleanup
        if receipt["cleanup"] != "empty":
            raise ValueError("Chrome process cleanup could not be verified")
        receipt["state"] = "complete"
