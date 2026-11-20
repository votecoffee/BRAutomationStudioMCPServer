"""Verify mapp View rendering and fresh counter samples in a private headless Chrome session."""

from dataclasses import dataclass
from decimal import Decimal
from http.client import HTTPConnection, HTTPException
from math import isfinite
from pathlib import Path
import json
import re
import time
from typing import ClassVar
from urllib.parse import urlsplit

from .arsim_smoke import HOST
from .native_build import fingerprint
from .windows_job import observed_process
from .windows_network import endpoints
from .windows_version import product_version

ASSEMBLY_WIDGET = "widgets/mapp6D/Assembly6D"
NUMBER = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
PAGE_SOCKET = re.compile(r"/devtools/page/[a-zA-Z0-9_-]+")
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


def widget_expression(assembly, counter):
    return f"""(() => {{
        const assembly = document.getElementById({json.dumps(assembly)});
        const counter = document.getElementById({json.dumps(counter)});
        return {{
            url: location.href,
            timeOrigin: performance.timeOrigin,
            widgetType: assembly?.getClientRects().length
                ? assembly.getAttribute('data-brease-widget') : null,
            value: counter?.getClientRects().length
                ? (counter.querySelector('input')?.value ?? counter.textContent)?.trim() : null
        }};
    }})()"""


def document_origin(state, url):
    origin = state.get("timeOrigin")
    if state.get("url") == url and type(origin) in {float, int} and isfinite(origin) and origin > 0:
        return origin
    return None


def collect_samples(page, expression, url, receipt, count, prior_origins=(), after=Decimal(0)):
    latest = after
    while time.monotonic() < page.deadline:
        result = page.read(expression)
        origin = document_origin(result, url)
        if origin is not None and origin not in prior_origins and result.get("widgetType") == ASSEMBLY_WIDGET:
            if "timeOrigin" in receipt and receipt["timeOrigin"] != origin:
                raise ValueError("The HMI document changed during counter sampling")
            receipt.update(timeOrigin=origin, widgetType=ASSEMBLY_WIDGET)
            value = result.get("value")
            if isinstance(value, str) and len(value) <= 64 and NUMBER.fullmatch(value):
                numeric = Decimal(value)
                if numeric > latest:
                    latest = numeric
                    receipt["counterSamples"].append(value)
                    if len(receipt["counterSamples"]) == count:
                        return
        time.sleep(0.2)
    raise TimeoutError("The displayed Assembly6D and changing numeric counter were not verified")


def verify_navigation(page, url, receipt):
    expression = widget_expression(receipt["assemblyWidgetId"], receipt["counterWidgetId"])
    page.navigate(url)
    receipt["state"] = "first_document"
    first = receipt["initial"] = {"counterSamples": []}
    collect_samples(page, expression, url, first, 3)

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
                    (first["timeOrigin"], blank), Decimal(first["counterSamples"][-1]))


@dataclass(frozen=True)
class ChromeProbe:
    executable: Path
    counter_widget: str
    assembly_widget: str | None
    timeout: int
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
        receipt.update(state="ready", assemblyWidgetId=assembly_dom_id(reviewed["widgets"], self.assembly_widget,
                                                                     self.counter_widget),
                       counterWidgetId=self.counter_widget,
                       chrome={"path": str(chrome), "version": version, "sha256": fingerprint(chrome)})

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
