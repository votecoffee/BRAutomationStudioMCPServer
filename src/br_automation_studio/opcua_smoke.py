"""Read a single increasing PLC counter from the reviewed ARsim instance."""

import asyncio
from dataclasses import dataclass
from importlib.metadata import version
from typing import ClassVar

from .arsim_smoke import HOST

CURRENT_READ = 0x01
INTEGER_NODE_TYPES = {
    "i=2": "SByte", "i=3": "Byte", "i=4": "Int16", "i=5": "UInt16",
    "i=6": "Int32", "i=7": "UInt32", "i=8": "Int64", "i=9": "UInt64",
}
INTEGER_TYPES = frozenset(INTEGER_NODE_TYPES.values())


def resolve_namespace(namespaces, uri):
    matches = [index for index, value in enumerate(namespaces) if value == uri]
    if len(matches) != 1 or not 1 <= matches[0] <= 65535:
        raise ValueError("The requested OPC UA namespace URI is missing, ambiguous, or reserved")
    return matches[0]


def good_scalar(data):
    status = data.StatusCode
    if status is None or not status.is_good():
        raise ValueError(f"OPC UA returned a value without Good quality ({status})")
    variant = data.Value
    if variant is None or variant.is_array or type(variant.Value) is not int:
        raise ValueError("OPC UA returned a value other than a scalar integer")
    return variant.Value, variant.VariantType.name


async def read_only_node(client, ua, guard, index, identifier, receipt):
    node_id = ua.NodeId(identifier, index, ua.NodeIdType.String)
    receipt.update(namespaceIndex=index, nodeId=node_id.to_string())
    node = client.get_node(node_id)
    guard()
    access, user_access = await node.read_attributes([ua.AttributeIds.AccessLevel, ua.AttributeIds.UserAccessLevel])
    for name, data in (("accessLevel", access), ("userAccessLevel", user_access)):
        value, variant = good_scalar(data)
        if variant != "Byte" or not 0 <= value <= 255:
            raise ValueError(f"OPC UA returned an invalid {name} attribute")
        receipt[name] = value
    if receipt["accessLevel"] != CURRENT_READ or receipt["userAccessLevel"] != CURRENT_READ:
        raise ValueError("OPC UA did not report CurrentRead alone for both the variable and this session")
    return node


async def inspect_counter(client, ua, guard, receipt, expected_status=(), *, wait_for_status=False):
    guard()
    receipt["state"] = "resolving_namespace"
    index = resolve_namespace(await client.get_namespace_array(), receipt["namespaceUri"])
    receipt["state"] = "reading_access"
    node = await read_only_node(client, ua, guard, index, receipt["stringIdentifier"], receipt)
    if expected_status:
        await inspect_status(client, ua, guard, receipt, expected_status, wait=wait_for_status)
    receipt["state"] = "sampling"
    latest = None
    while len(receipt["samples"]) < 3:
        guard()
        data = await node.read_data_value()
        value, variant = good_scalar(data)
        if variant not in INTEGER_TYPES or receipt.get("variantType", variant) != variant:
            raise ValueError("The OPC UA counter must retain one scalar integer type")
        receipt["variantType"] = variant
        if latest is not None and value < latest:
            raise ValueError("The OPC UA counter decreased during sampling")
        if latest is None or value > latest:
            latest = value
            stamp = data.SourceTimestamp
            receipt["samples"].append({"value": value, "statusCode": data.StatusCode.value,
                                       "sourceTimestamp": stamp.isoformat() if stamp is not None else None})
        if len(receipt["samples"]) < 3:
            await asyncio.sleep(0.2)
    if expected_status:
        await inspect_status(client, ua, guard, receipt, expected_status)
    guard()


@dataclass(frozen=True)
class StatusNode:
    identifier: str
    variant_type: str
    expected: int


async def inspect_status(client, ua, guard, receipt, expected_nodes, *, wait=False):
    receipt["state"] = "checking_status"
    records = receipt["statusValues"] = {}
    nodes = []
    for expected in expected_nodes:
        record = records[expected.identifier] = {}
        node = await read_only_node(client, ua, guard, receipt["namespaceIndex"], expected.identifier, record)
        nodes.append((expected, record, node))
    while True:
        matched = True
        for expected, record, node in nodes:
            guard()
            response = await node.read_data_value()
            value, variant = good_scalar(response)
            stamp = response.SourceTimestamp
            record.update(value=value, variantType=variant, statusCode=response.StatusCode.value,
                          sourceTimestamp=stamp.isoformat() if stamp is not None else None)
            if variant != expected.variant_type or (not wait and value != expected.expected):
                raise ValueError(f"Direct OPC UA status {expected.identifier} expected "
                                 f"{expected.expected} ({expected.variant_type}), read {value} ({variant})")
            matched &= value == expected.expected
        guard()
        if matched:
            return
        await asyncio.sleep(0.2)


async def observe_counter(endpoint, client_type, ua, listener, timeout, receipt, expected_status=(), *,
                          wait_for_status=False):
    client = client_type(endpoint, timeout=2, auto_reconnect=False)
    connected = False
    receipt["state"] = "waiting_for_listener"
    try:
        async with asyncio.timeout(timeout):
            while (owner := listener()) is None:
                await asyncio.sleep(0.2)
            receipt["ownedListener"] = owner

            def guard():
                if listener() != owner:
                    raise ValueError("ARsim lost its original OPC UA listener")

            guard()
            receipt["state"] = "opening_session"
            await client.connect()
            connected = True
            await inspect_counter(client, ua, guard, receipt, expected_status, wait_for_status=wait_for_status)
    finally:
        try:
            if connected:
                await client.disconnect()
        finally:
            client.disconnect_socket()
            receipt["clientDisconnected"] = connected
    receipt["state"] = "complete"


@dataclass(frozen=True)
class OpcUaProbe:
    namespace_uri: str
    string_identifier: str
    timeout: int
    expected_status: tuple[StatusNode, ...] = ()
    wait_for_status: bool = False
    key: ClassVar[str] = "opcUa"
    evidence: ClassVar[str] = "arsim_loopback_opcua"

    def prepare(self, reviewed, profile, receipt):
        port = profile["opcUaPort"]
        if port is None:
            raise ValueError("The reviewed PC_any configuration must enable an explicit loopback OPC UA listener")
        try:
            from asyncua import Client, ua
            from asyncua.ua.uaerrors import UaError
        except ImportError as error:
            raise ValueError("Install the br-automation-studio-tools opcua extra in this environment") from error

        endpoint = f"opc.tcp://{HOST}:{port}"
        receipt.update(state="ready", endpoint=endpoint, namespaceUri=self.namespace_uri,
                       stringIdentifier=self.string_identifier, clientVersion=version("asyncua"),
                       samples=[], clientDisconnected=False)

        def probe(session_dir, owned_listener):
            try:
                asyncio.run(observe_counter(endpoint, Client, ua, lambda: owned_listener(port), self.timeout, receipt,
                                            self.expected_status, wait_for_status=self.wait_for_status))
            except TimeoutError as error:
                raise ValueError(f"OPC UA timed out in phase {receipt['state']}") from error
            except UaError as error:
                raise ValueError(f"OPC UA failed in phase {receipt['state']} ({type(error).__name__}): {error}") from error

        return probe
