from enum import Enum
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from br_automation_studio.opcua_smoke import StatusNode, inspect_counter, inspect_status, observe_counter, resolve_namespace

URI = "urn:B&R/pv/"
IDENTIFIER = "::Pulse:Count"


class Attributes(Enum):
    AccessLevel = 17
    UserAccessLevel = 18


UA = SimpleNamespace(NodeId=Mock(), NodeIdType=SimpleNamespace(String="String"), AttributeIds=Attributes)
UA.NodeId.return_value.to_string.return_value = f"ns=2;s={IDENTIFIER}"


def response(value, variant="Int32", status=0):
    return SimpleNamespace(
        Value=SimpleNamespace(Value=value, VariantType=SimpleNamespace(name=variant), is_array=isinstance(value, list)),
        StatusCode=SimpleNamespace(value=status, is_good=lambda: status < 0x40000000),
        SourceTimestamp=None,
    )


class OpcUaSmokeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.node = Mock()
        self.node.read_attributes = AsyncMock(return_value=[response(1, "Byte"), response(1, "Byte")])
        self.node.read_data_value = AsyncMock()
        self.client = Mock()
        self.client.get_namespace_array = AsyncMock(return_value=["system", "server", URI])
        self.client.get_node.return_value = self.node
        self.receipt = {"namespaceUri": URI, "stringIdentifier": IDENTIFIER, "samples": []}

    def test_resolves_uri_and_rejects_invalid_indexes(self):
        self.assertEqual(resolve_namespace(["system", URI, "server"], URI), 1)
        for names in (["system"], ["system", URI, URI], [URI, "server"]):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "missing, ambiguous, or reserved"):
                resolve_namespace(names, URI)

    @patch("br_automation_studio.opcua_smoke.asyncio.sleep", new_callable=AsyncMock)
    async def test_records_three_advancing_samples_from_the_exact_string_node(self, sleep):
        self.node.read_data_value.side_effect = [response(n) for n in (0, 0, 1, 3)]
        await inspect_counter(self.client, UA, Mock(), self.receipt)
        UA.NodeId.assert_called_with(IDENTIFIER, 2, "String")
        self.node.read_attributes.assert_awaited_once_with([Attributes.AccessLevel, Attributes.UserAccessLevel])
        self.assertEqual([sample["value"] for sample in self.receipt["samples"]], [0, 1, 3])
        self.assertEqual(self.receipt["variantType"], "Int32")

    async def test_writable_metadata_stops_before_reading_the_counter(self):
        self.node.read_attributes.return_value = [response(3, "Byte"), response(1, "Byte")]
        with self.assertRaisesRegex(ValueError, "CurrentRead alone"):
            await inspect_counter(self.client, UA, Mock(), self.receipt)
        self.assertEqual(self.receipt["accessLevel"], 3)
        self.assertEqual(self.receipt["userAccessLevel"], 1)
        self.node.read_data_value.assert_not_awaited()

    async def test_bad_uncertain_and_non_integer_values_cannot_pass(self):
        for value in (response(7, status=0x40000000), response(7, status=0x80000000), response([7]),
                      response(True, "Boolean"), response(7.0, "Double")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.node.read_data_value.return_value = value
                await inspect_counter(self.client, UA, Mock(), self.receipt)
        self.assertEqual(self.receipt["samples"], [])

    async def test_status_reads_validate_quality_access_type_and_value(self):
        identifier = "::Pulse:Phase"
        expected = (StatusNode(identifier, "Int32", 10),)
        self.receipt["namespaceIndex"] = 2
        self.node.read_data_value.return_value = response(10)
        await inspect_status(self.client, UA, Mock(), self.receipt, expected)
        UA.NodeId.assert_called_with(identifier, 2, "String")
        record = self.receipt["statusValues"][identifier]
        self.assertEqual((record["value"], record["variantType"], record["accessLevel"], record["userAccessLevel"]),
                         (10, "Int32", 1, 1))
        for data in (response(11), response(10, "UInt32"), response(10, status=0x80000000)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.node.read_data_value.return_value = data
                await inspect_status(self.client, UA, Mock(), self.receipt, expected)
        self.node.read_attributes.return_value = [response(1, "Byte"), response(3, "Byte")]
        with self.assertRaisesRegex(ValueError, "CurrentRead alone"):
            await inspect_status(self.client, UA, Mock(), self.receipt, expected)

    @patch("br_automation_studio.opcua_smoke.asyncio.sleep", new_callable=AsyncMock)
    async def test_status_can_wait_for_initial_hold_then_checks_after_sampling(self, sleep):
        expected = (StatusNode("::Pulse:Phase", "Int32", 10),)
        self.node.read_data_value.side_effect = [response(n) for n in (0, 10, 42, 43, 44, 20)]
        with self.assertRaisesRegex(ValueError, "expected 10 .* read 20"):
            await inspect_counter(self.client, UA, Mock(), self.receipt, expected, wait_for_status=True)
        self.assertEqual([row["value"] for row in self.receipt["samples"]], [42, 43, 44])
        self.assertEqual(self.receipt["statusValues"]["::Pulse:Phase"]["value"], 20)

    async def test_disconnects_when_ownership_changes(self):
        owner = {"pid": 100}
        self.client.connect = AsyncMock()
        self.client.disconnect = AsyncMock()
        listener = Mock(side_effect=[owner, owner, {"pid": 101}])
        with self.assertRaisesRegex(ValueError, "lost its original OPC UA listener"):
            await observe_counter("opc.tcp://127.0.0.1:14840", Mock(return_value=self.client),
                                  UA, listener, 4, self.receipt)
        self.client.disconnect.assert_awaited_once()
        self.client.get_namespace_array.assert_not_awaited()
        self.assertTrue(self.receipt["clientDisconnected"])


if __name__ == "__main__":
    unittest.main()
