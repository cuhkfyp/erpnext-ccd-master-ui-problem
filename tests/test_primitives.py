import json
import unittest

from ccd_portal.primitives import canonical_json, is_valid_hkid, mask_value, normalize_value


class PrimitiveTests(unittest.TestCase):
	def test_normalization_is_deterministic(self):
		self.assertEqual(normalize_value("  Jane   DOE ", "Name"), "jane doe")
		self.assertEqual(normalize_value("User@EXAMPLE.invalid", "Email"), "user@example.invalid")
		self.assertEqual(normalize_value("+852 2345-6789", "Phone"), "85223456789")
		self.assertEqual(normalize_value("a 123456 (3)", "HKID"), "A1234563")
		self.assertEqual(normalize_value("2000-02-29", "Date"), "2000-02-29")

	def test_invalid_date_is_rejected(self):
		with self.assertRaises(ValueError):
			normalize_value("2025-02-29", "Date")

	def test_hkid_validation_requires_complete_check_digit(self):
		valid = "A123456" + "(3)"
		self.assertTrue(is_valid_hkid(valid))
		self.assertFalse(is_valid_hkid(valid[:-2] + "4)"))
		self.assertFalse(is_valid_hkid("123456"))

	def test_masking_strategies(self):
		values = {
			"Full": ("sensitive", "••••"),
			"Last 4": ("123456789", "•••••6789"),
			"Phone": ("+852 2345 6789", "•••• 6789"),
			"Email": ("user@example.invalid", "u•••@example.invalid"),
			"Year Only": ("1990-01-02", "1990"),
			"First Character": ("Jane", "J•••"),
		}
		for strategy, (value, expected) in values.items():
			with self.subTest(strategy=strategy):
				self.assertEqual(mask_value(value, strategy), expected)

	def test_canonical_json_never_depends_on_mapping_order(self):
		self.assertEqual(canonical_json({"b": 2, "a": 1}), '{"a":1,"b":2}')

	def test_fixture_contains_only_synthetic_namespace(self):
		with open("tests/fixtures/synthetic_records.json", encoding="utf-8") as fixture:
			rows = json.load(fixture)
		self.assertTrue(rows)
		self.assertTrue(all(row["ccd_reg_source"].startswith("SYNTHETIC-") for row in rows))
		self.assertTrue(all(".invalid" in row["email"] for row in rows))
