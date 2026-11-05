import unittest

from ccd_portal.source_identity import (
	canonical_source_id,
	same_source_lineage,
	source_id_for_registration,
	stable_source_id,
)


class SourceIdentityTests(unittest.TestCase):
	def test_numeric_frappe_amendment_suffix_is_removed(self):
		self.assertEqual(
			canonical_source_id("DT-NB10_event-management-34"),
			"DT-NB10_event-management",
		)

	def test_unsuffixed_registration_is_unchanged(self):
		self.assertEqual(canonical_source_id("DT-NB10_maxdb"), "DT-NB10_maxdb")

	def test_only_a_trailing_numeric_suffix_is_removed(self):
		self.assertEqual(canonical_source_id("SYNTHETIC-12-centre"), "SYNTHETIC-12-centre")
		self.assertEqual(canonical_source_id("SYNTHETIC-12-centre-7"), "SYNTHETIC-12-centre")

	def test_explicit_stable_source_key_takes_priority(self):
		self.assertEqual(
			source_id_for_registration(
				"HQ-vDB01-pjCTY-CUSTOMER-001_pjCTY-5",
				"HQ-vDB01_pjCTY_CUSTOMER",
			),
			"HQ-vDB01_pjCTY_CUSTOMER",
		)

	def test_explicit_stable_source_key_is_not_treated_as_an_amendment(self):
		self.assertEqual(stable_source_id(" SOURCE-7 "), "SOURCE-7")
		self.assertEqual(source_id_for_registration("REG-3", "SOURCE-7"), "SOURCE-7")

	def test_registration_name_remains_the_legacy_fallback(self):
		self.assertEqual(source_id_for_registration("LEGACY-SOURCE-4"), "LEGACY-SOURCE")

	def test_amendments_share_one_source_lineage(self):
		self.assertTrue(same_source_lineage("SYNTHETIC-SOURCE", "SYNTHETIC-SOURCE-34"))
		self.assertFalse(same_source_lineage("SYNTHETIC-A-1", "SYNTHETIC-B-1"))

	def test_empty_source_is_rejected_as_a_lineage(self):
		self.assertEqual(canonical_source_id(None), "")
		self.assertFalse(same_source_lineage("", ""))
