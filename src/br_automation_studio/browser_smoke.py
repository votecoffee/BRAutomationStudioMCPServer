"""Verify mapp View rendering and fresh counter samples in a private headless Chrome session."""

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
from math import hypot, inf, isfinite
from pathlib import Path
import json
import re
from tempfile import TemporaryDirectory
import time
from typing import ClassVar
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .arsim_smoke import HOST, WIDGET_BUNDLE_PATH, loopback_get
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
LAYOUT_ASSET_DIRECTORY = "layout-assets"
TRANSIENT_CONTEXT_ERRORS = {"Cannot find default execution context", "Execution context was destroyed."}
RESOURCE_LABELS = {
    "widgetBundle": "bundle",
    "startPageMap": "start page map",
    "startPageContent": "start page content",
}


def assembly_dom_id(widgets, selected, counter):
    available = [f"{item['contentId']}_{item['id']}" for item in widgets]
    if selected is None and len(available) == 1:
        selected = available[0]
    if selected is None or available.count(selected) != 1:
        raise ValueError("Select the unique browser DOM ID of a reviewed Assembly6D widget")
    if selected == counter:
        raise ValueError("Select a separate numeric counter widget")
    return selected


def layout_asset_path(link):
    if isinstance(link, str) and LINK_NAME.fullmatch(name := link.removeprefix("mapp6D::")):
        return f"/FileDevice:6DData/{name}.svg"
    return None


def layout_asset_paths(widgets, selected):
    widget = next(item for item in widgets if f"{item['contentId']}_{item['id']}" == selected)
    paths = {}
    for role, key in (("assembly", "assembly"), ("trafficManager", "manager")):
        link = widget.get(key)
        if role == "trafficManager" and link in (None, ""):
            continue
        if (path := layout_asset_path(link)) is None:
            return {}
        paths[role] = path
    return paths


def http_status(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def resource_observation(value, path):
    value = value if isinstance(value, dict) else {}
    status = http_status(value.get("httpStatus"))
    if status in (200, 304):
        state = "observed"
    elif status is not None and status >= 400:
        state = "request_failed"
    else:
        state = "unverified"
    return {"state": state, "path": path, "httpStatus": status}


def layout_observation(value, paths):
    value = value if isinstance(value, dict) else {}
    count = value.get("boundedSvgCount")
    count = count if type(count) is int and count >= 0 else None
    raw_statuses = value.get("statuses")
    observed = raw_statuses if isinstance(raw_statuses, dict) else {}
    statuses = {role: http_status(observed.get(role)) for role in paths}
    if any(status is not None and status >= 400 for status in statuses.values()):
        state = "request_failed"
    elif paths and count and all(status in (200, 304) for status in statuses.values()):
        state = "observed"
    else:
        state = "unverified"
    return {"state": state, "boundedSvgCount": count,
            "assets": {role: {"path": path, "httpStatus": statuses[role]} for role, path in paths.items()}}


def expected_traffic_labels(reviewed, start):
    if not start or start.get("trafficDetails", {}).get("state") != "matched":
        raise ValueError("Traffic label checks require a verified enabled Traffic Manager display option")
    compiled = start["compiled"]
    manager_id = (compiled["manager"] or "").removeprefix("mapp6D::")
    assembly = (compiled["assembly"] or "").removeprefix("mapp6D::")
    managers = [row for row in reviewed["trafficManagers"]
                if row["manager"] == manager_id and row["assembly"] == assembly]
    if len(managers) != 1:
        raise ValueError("Traffic label checks require one reviewed manager linked to the widget's assembly")
    names = [row["name"] for row in managers[0]["objects"] if row["type"] in {"Station", "Waypoint"}]
    if not names or len(set(names)) != len(names):
        raise ValueError("Traffic label checks require unique reviewed station or waypoint names")
    return sorted(names)


def label_overlap_observation(labels, verified):
    rectangles = []
    for name in verified:
        label = labels[name]
        left, top, width, height = (label.get(key) for key in ("leftCssPx", "topCssPx", "widthCssPx", "heightCssPx"))
        if (all(type(value) in (int, float) and isfinite(value) for value in (left, top, width, height))
                and width > 0 and height > 0):
            rectangles.append((left, left + width, top, top + height, name))
    rectangles.sort()
    pairs = []
    nearest = (inf, [])
    for index, (_, right, top, bottom, name) in enumerate(rectangles):
        for following in range(index + 1, len(rectangles)):
            other_left, _, other_top, other_bottom, other_name = rectangles[following]
            horizontal = max(0, other_left - right)
            # The sorted left edges bound the distance to every later rectangle.
            if horizontal > nearest[0]:
                break
            pair = sorted((name, other_name))
            vertical = max(0, other_top - bottom, top - other_bottom)
            nearest = min(nearest, (hypot(horizontal, vertical), pair))
            if other_left < right and other_top < bottom and top < other_bottom:
                pairs.append(pair)
    state = "observed"
    if len(rectangles) != len(labels):
        state = "unverified"
    elif pairs:
        state = "overlapping"
    return {"state": state, "checkedLabels": len(rectangles), "pairs": sorted(pairs),
            "minGapCssPx": nearest[0] if nearest[1] and state != "unverified" else None,
            "closestPair": nearest[1] if nearest[1] and state != "unverified" else None}


def label_edge_clearance(labels, verified):
    minima = {}
    for area in ("widget", "viewport"):
        distances = []
        for name in verified:
            clearance = labels[name].get("edgeClearance")
            entry = clearance.get(area) if isinstance(clearance, dict) else None
            if not isinstance(entry, dict):
                continue
            distance, edge = entry.get("distanceCssPx"), entry.get("edge")
            if type(distance) in (int, float) and isfinite(distance) and edge in ("left", "top", "right", "bottom"):
                distances.append((distance, name, edge))
        minima[area] = None
        if distances and len(distances) == len(labels):
            distance, name, edge = min(distances)
            minima[area] = {"distanceCssPx": distance, "label": name, "edge": edge}
    return minima


def traffic_label_observation(value, expected, minimum_height=None, require_in_bounds=False, require_no_overlap=False,
                              minimum_gap=None, minimum_edge_clearance=None):
    value = value if isinstance(value, dict) else {}
    observed = value.get("labels")
    observed = observed if isinstance(observed, dict) else {}
    labels = {name: observed.get(name) for name in expected}
    heights = {}
    for name, row in labels.items():
        if not isinstance(row, dict) or row.get("count") != 1 or row.get("text") != name or row.get("visible") is not True:
            continue
        height = row.get("heightCssPx")
        if type(height) in (float, int) and height > 0 and isfinite(height):
            heights[name] = height
    below = [] if minimum_height is None else [name for name, height in heights.items() if height < minimum_height]
    outside_widget = [name for name in heights if labels[name].get("insideWidget") is False]
    outside_viewport = [name for name in heights if labels[name].get("insideViewport") is False]
    overlap = label_overlap_observation(labels, heights)
    gap = overlap["minGapCssPx"]
    separation_required = require_no_overlap or minimum_gap is not None
    edge_clearance = label_edge_clearance(labels, heights)
    edge_missing = minimum_edge_clearance is not None and None in edge_clearance.values()
    bounds_missing = require_in_bounds and any(
        type(labels[name].get(key)) is not bool for name in heights for key in ("insideWidget", "insideViewport")
    )
    state = "observed"
    if (len(heights) != len(labels) or bounds_missing or edge_missing
            or (separation_required and overlap["state"] == "unverified")):
        state = "unverified"
    elif below:
        state = "below_minimum"
    elif require_in_bounds and (outside_widget or outside_viewport):
        state = "out_of_bounds"
    elif require_no_overlap and overlap["pairs"]:
        state = "overlapping"
    elif minimum_gap is not None and gap is not None and gap < minimum_gap:
        state = "gap_below_minimum"
    elif minimum_edge_clearance is not None and any(
        entry["distanceCssPx"] < minimum_edge_clearance for entry in edge_clearance.values()
    ):
        state = "edge_clearance_below_minimum"
    return {"state": state, "labels": labels, "viewport": value.get("viewport"),
            "viewportContent": value.get("viewportContent"), "minObservedHeightCssPx": min(heights.values(), default=None),
            "belowMinimum": below, "outsideWidget": outside_widget, "outsideViewport": outside_viewport,
            "overlap": overlap, "edgeClearance": edge_clearance}


def viewport_observation(value, requested):
    value = value if isinstance(value, dict) else {}
    observed = {key: value.get(key) for key in (*requested, "devicePixelRatio")}
    dimensions = [observed[key] for key in requested]
    state = "unverified"
    if all(type(size) is int and size > 0 for size in dimensions):
        state = "matched" if all(observed[key] == size for key, size in requested.items()) else "mismatched"
    return {"state": state, **observed}


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

    def set_viewport(self, width, height):
        reply = self.request("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 0, "mobile": False,
        })
        if "error" in reply or "result" not in reply:
            raise ValueError("Chrome could not apply the requested HMI viewport")

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


def widget_expression(assembly, counter, layout_paths=None, status_widgets=(), *, resource_paths=None,
                      traffic_labels=()):
    return f"""(() => {{
        const assembly = document.getElementById({json.dumps(assembly)});
        const assemblyRect = assembly?.getBoundingClientRect();
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
        const resources = Object.fromEntries(Object.entries({json.dumps(resource_paths or {})}).map(([key, path]) => {{
            const initiators = key === 'widgetBundle' ? ['script'] : ['xmlhttprequest', 'fetch'];
            const matching = performance.getEntriesByType('resource').findLast(entry => {{
                if (!initiators.includes(entry.initiatorType)) return false;
                const requested = new URL(entry.name);
                return requested.origin === location.origin && requested.pathname === path;
            }});
            return [key, matching ? {{httpStatus: matching.responseStatus}} : null];
        }}));
        const viewport = {{widthCssPx: innerWidth, heightCssPx: innerHeight, devicePixelRatio}};
        const labelNames = {json.dumps(list(traffic_labels))};
        const trafficLabels = labelNames.length && assembly ? (() => {{
            const root = document.compatMode === 'BackCompat' ? document.body : document.documentElement;
            const viewportRect = {{left: 0, top: 0, right: root.clientWidth, bottom: root.clientHeight}};
            const edgeClearance = (outer, rect) => {{
                const distances = {{
                    left: rect.left - outer.left, top: rect.top - outer.top,
                    right: outer.right - rect.right, bottom: outer.bottom - rect.bottom
                }};
                const [edge, distanceCssPx] = Object.entries(distances).reduce(
                    (closest, current) => current[1] < closest[1] ? current : closest
                );
                return {{edge, distanceCssPx}};
            }};
            const labels = new Map(labelNames.map(name => ['label_' + name, []]));
            for (const node of assembly.querySelectorAll('svg g.traffic-manager text[id]')) {{
                if (node.namespaceURI === 'http://www.w3.org/2000/svg') labels.get(node.id)?.push(node);
            }}
            const visible = (node, rect) => {{
                if (rect.width <= 0 || rect.height <= 0 || getComputedStyle(node).visibility !== 'visible') return false;
                for (let parent = node; parent; parent = parent.parentElement) {{
                    if (getComputedStyle(parent).opacity === '0') return false;
                }}
                return true;
            }};
            return {{
                viewport,
                viewportContent: {{widthCssPx: viewportRect.right, heightCssPx: viewportRect.bottom}},
                labels: Object.fromEntries(labelNames.map(name => {{
                    const matches = labels.get('label_' + name);
                    const node = matches.length === 1 ? matches[0] : null;
                    const rect = node?.getBoundingClientRect();
                    const shown = node ? visible(node, rect) : false;
                    const widget = shown ? edgeClearance(assemblyRect, rect) : null;
                    const viewportEdge = shown ? edgeClearance(viewportRect, rect) : null;
                    return [name, {{count: matches.length, text: node?.textContent.trim() ?? null,
                                    visible: shown, widthCssPx: rect?.width ?? null, heightCssPx: rect?.height ?? null,
                                    leftCssPx: rect?.left ?? null, topCssPx: rect?.top ?? null,
                                    insideWidget: widget ? widget.distanceCssPx >= 0 : null,
                                    insideViewport: viewportEdge ? viewportEdge.distanceCssPx >= 0 : null,
                                    edgeClearance: {{widget, viewport: viewportEdge}}}}];
                }}))
            }};
        }})() : null;
        return {{
            ...resources,
            url: location.href,
            timeOrigin: performance.timeOrigin,
            widgetType: assembly?.getClientRects().length
                ? assembly.getAttribute('data-brease-widget') : null,
            assemblyBounds: assemblyRect ? {{widthCssPx: assemblyRect.width, heightCssPx: assemblyRect.height}} : null,
            value: read({json.dumps(counter)}),
            statusValues: Object.fromEntries({json.dumps(list(status_widgets))}.map(id => [id, read(id)])),
            viewport,
            trafficLabels,
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
                    *, layout_paths=None, require_layout=False, check_assembly=True, expected_status=None,
                    resource_paths=None, traffic_labels=(), minimum_label_height=None, requested_viewport=None,
                    require_label_bounds=False, require_label_separation=False, minimum_label_gap=None,
                    minimum_label_edge_clearance=None):
    latest = after
    resource_paths = resource_paths or {}
    while time.monotonic() < page.deadline:
        result = page.read(expression)
        origin = document_origin(result, url)
        current_document = origin is not None and origin not in prior_origins
        if current_document and requested_viewport:
            viewport = receipt["viewport"] = viewport_observation(result.get("viewport"), requested_viewport)
            if viewport["state"] != "matched":
                time.sleep(0.2)
                continue
        widget_matches = not check_assembly or result.get("widgetType") == ASSEMBLY_WIDGET
        if current_document and widget_matches:
            if "timeOrigin" in receipt and receipt["timeOrigin"] != origin:
                raise ValueError("The HMI document changed during counter sampling")
            receipt["timeOrigin"] = origin
            if check_assembly:
                receipt["widgetType"] = ASSEMBLY_WIDGET
                bounds = result.get("assemblyBounds")
                if isinstance(bounds, dict) and all(
                    type(size) in (float, int) and isfinite(size) and size >= 0
                    for size in (bounds.get("widthCssPx"), bounds.get("heightCssPx"))
                ):
                    receipt["assemblyBounds"] = bounds
                else:
                    receipt.pop("assemblyBounds", None)
            if layout_paths:
                receipt["layout"] = layout_observation(result.get("layout"), layout_paths)
            if traffic_labels:
                receipt["trafficLabels"] = traffic_label_observation(
                    result.get("trafficLabels"), traffic_labels, minimum_label_height, require_label_bounds,
                    require_label_separation, minimum_label_gap, minimum_label_edge_clearance)
            for key, path in resource_paths.items():
                receipt[key] = resource_observation(result.get(key), path)
            value = result.get("value")
            status_matches = not expected_status or check_document_status(result, receipt, expected_status)
            if status_matches and len(receipt["counterSamples"]) < count:
                numeric = numeric_text(value)
                if numeric is not None and numeric > latest:
                    latest = numeric
                    receipt["counterSamples"].append(value)
            if (status_matches and len(receipt["counterSamples"]) == count
                    and all(receipt[key]["state"] == "observed" for key in resource_paths)
                    and (not traffic_labels or receipt["trafficLabels"]["state"] == "observed")
                    and (not require_layout or receipt.get("layout", {}).get("state") == "observed")):
                return
        time.sleep(0.2)
    if (viewport := receipt.get("viewport")) and viewport["state"] != "matched":
        width, height = requested_viewport["widthCssPx"], requested_viewport["heightCssPx"]
        raise TimeoutError(f"The browser did not report the requested {width} × {height} CSS px viewport")
    if len(receipt["counterSamples"]) == count:
        for key in resource_paths:
            if receipt[key]["state"] != "observed":
                raise TimeoutError(f"A successful same-origin mapp View {RESOURCE_LABELS[key]} request was not verified")
        if require_layout and receipt.get("layout", {}).get("state") != "observed":
            raise TimeoutError("Successful 6DData SVG requests and an inline SVG with layout bounds were not verified")
        if traffic_labels:
            observed = receipt["trafficLabels"]
            if observed["state"] == "below_minimum":
                names = ", ".join(observed["belowMinimum"])
                raise TimeoutError(f"Traffic label heights below {minimum_label_height} CSS px: {names}")
            if observed["state"] == "out_of_bounds":
                names = ", ".join(sorted(set(observed["outsideWidget"]) | set(observed["outsideViewport"])))
                raise TimeoutError(f"Traffic labels extend outside the Assembly6D widget or browser viewport: {names}")
            if observed["state"] == "overlapping":
                first, second = observed["overlap"]["pairs"][0]
                raise TimeoutError(f"Traffic label rectangles overlap: {first} and {second}")
            if observed["state"] == "gap_below_minimum":
                first, second = observed["overlap"]["closestPair"]
                raise TimeoutError(f"Traffic label gap below {minimum_label_gap} CSS px: {first} and {second}")
            if observed["state"] == "edge_clearance_below_minimum":
                area, closest = min(observed["edgeClearance"].items(), key=lambda item: item[1]["distanceCssPx"])
                raise TimeoutError(f"Traffic label edge clearance below {minimum_label_edge_clearance} CSS px"
                                   f" for {closest['label']} at the {area} {closest['edge']} edge")
            if observed["state"] != "observed":
                raise TimeoutError("The expected station and waypoint labels were not verified in the Assembly6D widget")
    if expected_status:
        raise TimeoutError("The expected displayed status values and changing numeric counter were not verified")
    raise TimeoutError("The displayed Assembly6D and changing numeric counter were not verified")


def verify_navigation(page, url, receipt):
    paths = receipt.get("layoutPaths")
    required = receipt.get("requireLayoutAssets", False)
    resources = {}
    if receipt.get("requireWidgetBundle"):
        resources["widgetBundle"] = WIDGET_BUNDLE_PATH
    if client_map := receipt.get("startPageMapPath"):
        resources["startPageMap"] = client_map
    if content := receipt.get("startPageContentPath"):
        resources["startPageContent"] = content
    expected = receipt.get("expectedStatus", {})
    assembly = receipt["assemblyWidgetId"]
    labels = receipt.get("expectedTrafficLabels", ())
    viewport = receipt.get("requestedViewport")
    options = {"layout_paths": paths, "require_layout": required, "check_assembly": assembly is not None,
               "expected_status": expected, "resource_paths": resources, "traffic_labels": labels,
               "minimum_label_height": receipt.get("minimumTrafficLabelHeightCssPx"),
               "requested_viewport": viewport, "require_label_bounds": receipt.get("requireTrafficLabelsInBounds", False),
               "require_label_separation": receipt.get("requireNonOverlappingTrafficLabels", False),
               "minimum_label_gap": receipt.get("minimumTrafficLabelGapCssPx"),
               "minimum_label_edge_clearance": receipt.get("minimumTrafficLabelEdgeClearanceCssPx")}
    expression = widget_expression(assembly, receipt["counterWidgetId"], paths, expected, resource_paths=resources,
                                   traffic_labels=labels)
    if viewport:
        page.set_viewport(viewport["widthCssPx"], viewport["heightCssPx"])
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
                        "file": f"{LAYOUT_ASSET_DIRECTORY}/{role}.svg"}

    root = session_dir.resolve(strict=True)
    target = root / LAYOUT_ASSET_DIRECTORY
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
    require_widget_bundle: bool = False
    require_traffic_labels: bool = False
    minimum_label_height: int | None = None
    viewport: tuple[int, int] | None = None
    require_label_bounds: bool = False
    require_label_separation: bool = False
    minimum_label_gap: int | None = None
    minimum_label_edge_clearance: int | None = None
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
        start = reviewed.get("startPageWidget")
        selected = self.assembly_widget or (start["domId"] if start else None)
        widget = None
        if self.check_assembly or start:
            widget = assembly_dom_id(reviewed["widgets"], selected, self.counter_widget)
        paths = layout_asset_paths(reviewed["widgets"], widget) if widget is not None else {}
        check_labels = (self.require_traffic_labels or self.minimum_label_height is not None
                        or self.require_label_bounds or self.require_label_separation or self.minimum_label_gap is not None
                        or self.minimum_label_edge_clearance is not None)
        require_layout = self.require_layout or self.capture_layout or check_labels
        if require_layout and not paths:
            raise ValueError("The reviewed widget has no supported 6DData SVG paths")
        receipt.update(state="ready", assemblyWidgetId=widget, layoutPaths=paths, requireLayoutAssets=require_layout,
                       counterWidgetId=self.counter_widget,
                       chrome={"path": str(chrome), "version": version, "sha256": fingerprint(chrome)})
        if start:
            if start["state"] != "matched" or start["domId"] != widget:
                raise ValueError("The start page review must match the browser's Assembly6D widget")
            receipt["startPageMapPath"] = start["compiled"]["clientMapUrl"]
            if "trafficDetails" in start:
                receipt["startPageContentPath"] = start["compiled"]["contentUrl"]
        if check_labels:
            if "trafficManager" not in paths:
                raise ValueError("Traffic label checks require a Traffic Manager SVG path")
            receipt["expectedTrafficLabels"] = expected_traffic_labels(reviewed, start)
        if self.require_label_bounds:
            receipt["requireTrafficLabelsInBounds"] = True
        if self.require_label_separation:
            receipt["requireNonOverlappingTrafficLabels"] = True
        if self.minimum_label_gap is not None:
            receipt["minimumTrafficLabelGapCssPx"] = self.minimum_label_gap
        if self.minimum_label_edge_clearance is not None:
            receipt["minimumTrafficLabelEdgeClearanceCssPx"] = self.minimum_label_edge_clearance
        if self.minimum_label_height is not None:
            receipt["minimumTrafficLabelHeightCssPx"] = self.minimum_label_height
        if self.viewport is not None:
            width, height = self.viewport
            receipt["requestedViewport"] = {"widthCssPx": width, "heightCssPx": height}
        if self.require_widget_bundle:
            if widget is None:
                raise ValueError("The bundle check requires a reviewed Assembly6D widget")
            receipt["requireWidgetBundle"] = True
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
