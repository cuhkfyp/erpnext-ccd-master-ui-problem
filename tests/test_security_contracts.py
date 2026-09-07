import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class FakeDatabase:
	def __init__(self, *, enabled=0, preview=False, authority=None, failure=None):
		self.enabled = enabled
		self.preview = preview
		self.authority = authority
		self.failure = failure

	def _check(self):
		if self.failure:
			raise self.failure

	def get_single_value(self, doctype, fieldname):
		self._check()
		return self.enabled

	def exists(self, doctype, filters):
		self._check()
		return self.preview

	def get_value(self, doctype, filters, fieldname, **kwargs):
		self._check()
		return self.authority


def load_security(database, user="staff@example.invalid"):
	frappe = ModuleType("frappe")
	frappe._ = lambda value: value
	frappe.db = database
	frappe.local = SimpleNamespace(message_log=["existing-message"])
	frappe.session = SimpleNamespace(user=user)

	utils = ModuleType("frappe.utils")
	utils.cint = lambda value: int(value or 0)
	utils.nowdate = lambda: "2026-09-07"

	module_name = "ccd_portal_security_contract_test"
	path = ROOT / "ccd_portal" / "security.py"
	spec = importlib.util.spec_from_file_location(module_name, path)
	module = importlib.util.module_from_spec(spec)
	with patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils, module_name: module}):
		spec.loader.exec_module(module)
	return module, frappe


class AppMenuPermissionTests(unittest.TestCase):
	def test_denied_user_returns_false_without_a_pending_popup(self):
		security, frappe = load_security(FakeDatabase(enabled=0, preview=False, authority=None))
		security.require_context = lambda *args, **kwargs: self.fail("throwing API guard was called")

		self.assertFalse(security.has_portal_permission())
		self.assertEqual(frappe.local.message_log, ["existing-message"])

	def test_enabled_profile_and_named_preview_are_allowed(self):
		for database in (
			FakeDatabase(enabled=1, authority="Reader"),
			FakeDatabase(enabled=0, preview=True, authority="Data Steward"),
		):
			with self.subTest(database=database.__dict__):
				security, frappe = load_security(database)
				self.assertTrue(security.has_portal_permission())
				self.assertEqual(frappe.local.message_log, ["existing-message"])

	def test_database_failure_fails_closed_without_a_pending_popup(self):
		security, frappe = load_security(FakeDatabase(failure=RuntimeError("database unavailable")))

		self.assertFalse(security.has_portal_permission())
		self.assertEqual(frappe.local.message_log, ["existing-message"])


if __name__ == "__main__":
	unittest.main()
