import unittest

from ccd_portal.source_identity import canonical_source_id, same_source_lineage


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

	def test_amendments_share_one_source_lineage(self):
		self.assertTrue(same_source_lineage("SYNTHETIC-SOURCE", "SYNTHETIC-SOURCE-34"))
		self.assertFalse(same_source_lineage("SYNTHETIC-A-1", "SYNTHETIC-B-1"))

	def test_empty_source_is_rejected_as_a_lineage(self):
		self.assertEqual(canonical_source_id(None), "")
		self.assertFalse(same_source_lineage("", ""))
