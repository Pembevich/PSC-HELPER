"""The discovery boundary must not expose or execute unselected capabilities."""

import copy
import unittest

from agent_tools import (
    FINISH_RESPONSE_TOOL,
    MAX_DISCOVERY_TOOLS,
    compact_tool_catalog,
    discover_tool_schemas,
    make_discovery_tool,
)
from cogs.ai_tools import POS_AI_TOOLS


def _schema(name, description="Описание инструмента"):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
                "additionalProperties": False,
            },
        },
    }


class AgentCatalogTests(unittest.TestCase):
    def setUp(self):
        self.schemas = {
            "lookup": _schema("lookup"),
            "assign": _schema("assign"),
            "hidden": _schema("hidden", "Секретный административный инструмент"),
        }
        self.eligible = frozenset({"lookup", "assign"})

    def test_native_schema_limits_selection_to_authenticated_catalog(self):
        tool = make_discovery_tool(self.eligible)
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "discover_tools")
        params = tool["function"]["parameters"]
        names = params["properties"]["names"]
        self.assertEqual(params["required"], ["names"])
        self.assertIs(params["additionalProperties"], False)
        self.assertEqual(names["type"], "array")
        self.assertEqual(names["items"]["enum"], ["assign", "lookup"])
        self.assertEqual((names["minItems"], names["maxItems"]), (1, 12))
        self.assertIs(names["uniqueItems"], True)

    def test_finish_response_declares_explicit_outcome_and_evidence(self):
        self.assertEqual(FINISH_RESPONSE_TOOL["type"], "function")
        function = FINISH_RESPONSE_TOOL["function"]
        self.assertEqual(function["name"], "finish_response")
        params = function["parameters"]
        self.assertEqual(set(params["required"]), {"text", "outcome", "evidence_call_ids"})
        self.assertIs(params["additionalProperties"], False)
        properties = params["properties"]
        self.assertEqual(properties["text"]["maxLength"], 6000)
        self.assertEqual(properties["outcome"]["enum"], ["answered", "completed", "partial", "blocked"])
        self.assertEqual(properties["evidence_call_ids"]["items"], {"type": "string"})
        # Refused attempts also need evidence even after the 24-action budget.
        self.assertEqual(properties["evidence_call_ids"]["maxItems"], 384)

    def test_real_role_tool_is_discoverable_without_matching_user_keywords(self):
        schemas = {tool["function"]["name"]: tool for tool in POS_AI_TOOLS}
        # No text or intent classifier is required to select a capability.
        selected, error = discover_tool_schemas(
            {"names": ["list_members", "list_roles", "add_role"]},
            schemas,
            frozenset(schemas),
        )
        self.assertIsNone(error)
        self.assertEqual([item["function"]["name"] for item in selected],
                         ["list_members", "list_roles", "add_role"])
        self.assertIn("role_id_or_name", selected[-1]["function"]["parameters"]["properties"])

    def test_order_is_preserved_and_calls_can_discover_different_tools(self):
        first, error = discover_tool_schemas({"names": ["lookup", "assign"]}, self.schemas, self.eligible)
        second, error2 = discover_tool_schemas({"names": ["assign"]}, self.schemas, self.eligible)
        self.assertIsNone(error)
        self.assertIsNone(error2)
        self.assertEqual([s["function"]["name"] for s in first], ["lookup", "assign"])
        self.assertEqual([s["function"]["name"] for s in second], ["assign"])

    def test_discovery_does_not_mutate_nested_schema_or_other_discovery_results(self):
        original = copy.deepcopy(self.schemas)
        first, _ = discover_tool_schemas({"names": ["assign"]}, self.schemas, self.eligible)
        first[0]["function"]["parameters"]["properties"]["target"]["type"] = "number"
        first[0]["function"]["parameters"]["required"].clear()
        second, _ = discover_tool_schemas({"names": ["assign"]}, self.schemas, self.eligible)
        self.assertEqual(self.schemas, original)
        self.assertEqual(second, [original["assign"]])

    def test_malformed_native_arguments_are_rejected(self):
        invalid = [
            None, [], "{\"names\":[\"assign\"]}", {},
            {"names": ["assign"], "execute": True},
            {"names": None}, {"names": "assign"}, {"names": ("assign",)},
            {"names": []}, {"names": [None]}, {"names": [True]},
            {"names": [1]}, {"names": [{}]}, {"names": [["assign"]]},
            {"names": [""]}, {"names": ["assign", "assign"]},
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                selected, error = discover_tool_schemas(arguments, self.schemas, self.eligible)
                self.assertEqual(selected, [])
                self.assertIsInstance(error, str)
                self.assertTrue(error)

    def test_unknown_or_hidden_names_reject_the_entire_batch(self):
        for name in ["hidden", "unknown", "ASSIGN", " assign", "assign ", "discover_tools"]:
            with self.subTest(name=name):
                selected, error = discover_tool_schemas({"names": ["lookup", name]}, self.schemas, self.eligible)
                self.assertEqual(selected, [])
                self.assertTrue(error)

    def test_no_eligible_tools_cannot_be_discovered(self):
        selected, error = discover_tool_schemas({"names": ["lookup"]}, self.schemas, frozenset())
        self.assertEqual(selected, [])
        self.assertTrue(error)
        self.assertEqual(compact_tool_catalog(self.schemas, frozenset(), frozenset()), "")

    def test_eligible_but_missing_schema_rejects_entire_batch(self):
        selected, error = discover_tool_schemas({"names": ["lookup", "absent"]}, self.schemas,
                                                self.eligible | {"absent"})
        self.assertEqual(selected, [])
        self.assertTrue(error)

    def test_mismatched_schema_cannot_expose_hidden_tool_under_eligible_key(self):
        self.schemas["lookup"] = self.schemas["hidden"]
        selected, error = discover_tool_schemas({"names": ["assign", "lookup"]}, self.schemas, self.eligible)
        self.assertEqual(selected, [])
        self.assertTrue(error)
        self.assertNotIn("Секретный", compact_tool_catalog(self.schemas, self.eligible, {"assign"}))

    def test_discovery_batch_size_is_bounded(self):
        schemas = {f"tool_{i}": _schema(f"tool_{i}") for i in range(MAX_DISCOVERY_TOOLS + 1)}
        eligible = frozenset(schemas)
        accepted, error = discover_tool_schemas({"names": list(schemas)[:MAX_DISCOVERY_TOOLS]}, schemas, eligible)
        self.assertEqual(len(accepted), MAX_DISCOVERY_TOOLS)
        self.assertIsNone(error)
        rejected, error = discover_tool_schemas({"names": list(schemas)}, schemas, eligible)
        self.assertEqual(rejected, [])
        self.assertTrue(error)

    def test_compact_catalog_is_stable_and_hides_ineligible_schemas(self):
        original = copy.deepcopy(self.schemas)
        catalog = compact_tool_catalog(self.schemas, self.eligible, {"assign", "hidden"})
        self.assertEqual(catalog.splitlines(), [
            "assign [write]: Описание инструмента",
            "lookup [read]: Описание инструмента",
        ])
        self.assertNotIn("hidden", catalog)
        self.assertNotIn("Секретный", catalog)
        self.assertEqual(self.schemas, original)

    def test_description_is_flattened_and_limited_to_140_characters(self):
        self.schemas["lookup"]["function"]["description"] = "  Найти\n\tданные  " + "я" * 200
        catalog = compact_tool_catalog(self.schemas, {"lookup", "absent"}, set())
        description = catalog.split(": ", 1)[1]
        self.assertTrue(description.startswith("Найти данные "))
        self.assertEqual(len(description), 140)
        self.assertNotIn("\n", description)

    def test_discovery_schema_results_are_independent_between_actors(self):
        first = make_discovery_tool(self.eligible)
        first["function"]["parameters"]["properties"]["names"]["items"]["enum"].append("hidden")
        second = make_discovery_tool(frozenset({"lookup"}))
        self.assertEqual(second["function"]["parameters"]["properties"]["names"]["items"]["enum"], ["lookup"])


if __name__ == "__main__":
    unittest.main()
