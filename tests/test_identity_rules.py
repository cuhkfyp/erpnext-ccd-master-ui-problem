import unittest

from ccd_portal.identity_rules import assignment_is_eligible, bounded_record_order
from ccd_portal.primitives import is_valid_unified_person_number, unified_person_check_digit


class UnifiedPersonNumberTests(unittest.TestCase):
	def test_exact_format_and_check_digit(self):
		payload = "000000123"
		number = f"HKSR-U{payload}{unified_person_check_digit(payload)}"
		self.assertTrue(is_valid_unified_person_number(number))
		self.assertFalse(is_valid_unified_person_number(number[:-1] + str((int(number[-1]) + 1) % 10)))
		self.assertFalse(is_valid_unified_person_number("HKSR-U0000000000"))
		self.assertFalse(is_valid_unified_person_number(number.lower()))


class EligibilityTests(unittest.TestCase):
	person = {"status": "Active", "canonical_person": None}
	members = [{"ccd_master": "SYNTHETIC-A"}, {"ccd_master": "SYNTHETIC-B"}]

	def test_active_group_is_eligible_from_either_member(self):
		rows = [
			{"ccd_master": "SYNTHETIC-A", "identity_group": "SYNTHETIC-G1", "membership_status": "Active", "group_status": "Active"},
			{"ccd_master": "SYNTHETIC-B", "identity_group": "SYNTHETIC-G1", "membership_status": "Active", "group_status": "Active"},
		]
		self.assertEqual(assignment_is_eligible(self.person, self.members, rows), (True, "eligible"))

	def test_reinsert_without_a_current_group_is_eligible(self):
		self.assertEqual(assignment_is_eligible(self.person, self.members, []), (True, "eligible"))

	def test_ended_or_missing_historical_group_does_not_veto(self):
		rows = [
			{"ccd_master": "SYNTHETIC-A", "identity_group": "SYNTHETIC-OLD", "membership_status": "Active", "group_status": "Ended"},
			{"ccd_master": "SYNTHETIC-B", "identity_group": "SYNTHETIC-MISSING", "membership_status": "Active", "group_status": None},
		]
		self.assertEqual(assignment_is_eligible(self.person, self.members, rows), (True, "eligible"))

	def test_revalidation_vetoes_the_whole_person(self):
		rows = [{"ccd_master": "SYNTHETIC-A", "identity_group": "SYNTHETIC-G1", "membership_status": "Needs Revalidation", "group_status": "Needs Revalidation"}]
		self.assertEqual(assignment_is_eligible(self.person, self.members, rows)[1], "needs_revalidation")

	def test_governed_split_or_conflicting_groups_vetoes_expansion(self):
		rows = [
			{"ccd_master": "SYNTHETIC-A", "identity_group": "SYNTHETIC-G1", "membership_status": "Active", "group_status": "Active"},
			{"ccd_master": "SYNTHETIC-B", "identity_group": "SYNTHETIC-G2", "membership_status": "Active", "group_status": "Active"},
		]
		self.assertEqual(assignment_is_eligible(self.person, self.members, rows)[1], "conflicting_identity_groups")

	def test_duplicate_active_unified_membership_vetoes_expansion(self):
		members = [*self.members, {"ccd_master": "SYNTHETIC-A"}]
		self.assertEqual(assignment_is_eligible(self.person, members, [])[1], "duplicate_unified_membership")


class BoundedResultTests(unittest.TestCase):
	def test_direct_matches_win_then_related_are_deduplicated_deterministically(self):
		direct = ["SYNTHETIC-09", "SYNTHETIC-01", "SYNTHETIC-09"]
		related = [
			["SYNTHETIC-03", "SYNTHETIC-02", "SYNTHETIC-01"],
			[*(f"SYNTHETIC-{index:02}" for index in range(2, 25))],
		]
		ordered, truncated = bounded_record_order(direct, related, 20)
		self.assertEqual(ordered[:4], ["SYNTHETIC-09", "SYNTHETIC-01", "SYNTHETIC-02", "SYNTHETIC-03"])
		self.assertEqual(len(ordered), 20)
		self.assertEqual(len(ordered), len(set(ordered)))
		self.assertTrue(truncated)


if __name__ == "__main__":
	unittest.main()
