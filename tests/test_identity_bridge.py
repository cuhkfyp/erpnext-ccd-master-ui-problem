import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from ccd_portal.primitives import unified_person_check_digit


ROOT = Path(__file__).resolve().parents[1]


class AttrDict(dict):
	__getattr__ = dict.get


def number(sequence):
	payload = f"{sequence:09d}"
	return f"HKSR-U{payload}{unified_person_check_digit(payload)}"


class IdentityDatabase:
	canonical = number(123)
	alias = number(122)

	def sql(self, query, values=(), **kwargs):
		if "information_schema" in query:
			return []
		if "WHERE p.unified_person_number" in query:
			return [
				AttrDict(
					name=self.alias,
					unified_person_number=self.alias,
					status="Alias",
					canonical_person=self.canonical,
					current_identity_group=None,
					current_group_status=None,
				)
			]
		if "FROM `tabCCD Unified Person Alias`" in query:
			return [AttrDict(canonical_person=self.canonical)]
		if "WHERE p.name = %s" in query:
			return [self._person()]
		if "SELECT m.ccd_master, p.name" in query:
			return [AttrDict(ccd_master=record, **self._person()) for record in values]
		if "SELECT m.name, m.unified_person, m.ccd_master" in query:
			return [
				AttrDict(name="UM-A", unified_person=self.canonical, ccd_master="SYNTHETIC-A"),
				AttrDict(name="UM-B", unified_person=self.canonical, ccd_master="SYNTHETIC-B"),
			]
		if "SELECT m.name, m.ccd_master" in query:
			return [AttrDict(name="UM-A", ccd_master="SYNTHETIC-A"), AttrDict(name="UM-B", ccd_master="SYNTHETIC-B")]
		if "SELECT name, unified_person, ccd_master" in query:
			return [
				AttrDict(name="UM-A", unified_person=self.canonical, ccd_master="SYNTHETIC-A"),
				AttrDict(name="UM-B", unified_person=self.canonical, ccd_master="SYNTHETIC-B"),
			]
		if "SELECT name, ccd_master" in query:
			return [AttrDict(name="UM-A", ccd_master="SYNTHETIC-A"), AttrDict(name="UM-B", ccd_master="SYNTHETIC-B")]
		if "FROM `tabCCD Identity Membership`" in query:
			return [
				AttrDict(ccd_master="SYNTHETIC-A", identity_group="SYNTHETIC-G", membership_status="Active", group_status="Active"),
				AttrDict(ccd_master="SYNTHETIC-B", identity_group="SYNTHETIC-G", membership_status="Active", group_status="Active"),
			]
		raise AssertionError(query)

	def _person(self):
		return AttrDict(
			name=self.canonical,
			unified_person_number=self.canonical,
			status="Active",
			canonical_person=None,
			current_identity_group="SYNTHETIC-G",
			current_group_status="Active",
		)


def load_identity(database):
	frappe = ModuleType("frappe")
	frappe.db = database
	utils = ModuleType("frappe.utils")
	utils.cint = lambda value: int(value or 0)
	utils.now_datetime = lambda: "2026-10-07 00:00:00"
	security = ModuleType("ccd_portal.security")
	security.PortalContext = object
	security.protected_digest = lambda value, purpose: "0" * 64
	module_name = "ccd_portal_identity_bridge_test"
	spec = importlib.util.spec_from_file_location(module_name, ROOT / "ccd_portal" / "identity.py")
	module = importlib.util.module_from_spec(spec)
	with patch.dict(
		sys.modules,
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"ccd_portal.security": security,
			module_name: module,
		},
	):
		spec.loader.exec_module(module)
	return module


class IdentityBridgeTests(unittest.TestCase):
	def test_active_alias_resolves_silently_to_eligible_canonical_person(self):
		database = IdentityDatabase()
		identity = load_identity(database)
		person = identity.person_for_number(database.alias)
		self.assertEqual(person["name"], database.canonical)
		self.assertEqual(person["unified_person_number"], database.canonical)
		self.assertEqual(person["ccd_master_records"], ["SYNTHETIC-A", "SYNTHETIC-B"])

	def test_either_member_resolves_the_same_live_person(self):
		database = IdentityDatabase()
		identity = load_identity(database)
		left = identity.persons_for_records(["SYNTHETIC-A"])["SYNTHETIC-A"]
		right = identity.persons_for_records(["SYNTHETIC-B"])["SYNTHETIC-B"]
		self.assertEqual(left["name"], right["name"])
		self.assertEqual(left["ccd_master_records"], right["ccd_master_records"])

	def test_schema_readiness_fails_closed_on_identity_outage(self):
		class FailedDatabase:
			def sql(self, *args, **kwargs):
				raise RuntimeError("synthetic outage")

		status = load_identity(FailedDatabase()).schema_status()
		self.assertFalse(status["ready"])
		self.assertTrue(status["check_failed"])


if __name__ == "__main__":
	unittest.main()
