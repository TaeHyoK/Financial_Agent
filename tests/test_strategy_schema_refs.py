import unittest
from jsonschema import Draft202012Validator, ValidationError
from Agent_Team.Strategy_Agent.contracts_v5 import strategy_decision_response_format_v5


class StrategySchemaRefTests(unittest.TestCase):
    def test_large_catalog_keeps_all_allowed_ids_without_repeated_enums(self):
        keys = [f"NEWS_{index}" for index in range(100)]
        schema = strategy_decision_response_format_v5({"evidence_cards": {
            key: {"domain": "news"} for key in keys}})["json_schema"]["schema"]
        Draft202012Validator.check_schema(schema)
        self.assertEqual(set(schema["$defs"]["evidence_card_key"]["enum"]), set(keys))
        def count(value):
            if isinstance(value, list):
                return sum(count(item) for item in value)
            if isinstance(value, dict):
                return len(value.get("enum", [])) + sum(count(v) for k, v in value.items() if k != "enum")
            return 0
        self.assertLess(count(schema), 1000)
        reference = schema["properties"]["strategy_brief"]["properties"]["thesis"]["properties"]["card_keys"]
        validator = Draft202012Validator({"$defs": schema["$defs"], **reference})
        validator.validate(keys)
        with self.assertRaises(ValidationError):
            validator.validate(["UNKNOWN_ID"])


if __name__ == "__main__":
    unittest.main()
