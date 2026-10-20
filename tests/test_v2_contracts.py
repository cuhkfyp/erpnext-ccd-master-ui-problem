import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCTYPE_ROOT = ROOT / "ccd_portal" / "ccd_portal" / "doctype"


class SamePersonRepositoryContracts(unittest.TestCase):
	def test_db_connector_remains_optional_and_schema_is_runtime_gated(self):
		hooks = (ROOT / "ccd_portal" / "hooks.py").read_text()
		identity = (ROOT / "ccd_portal" / "identity.py").read_text()
		self.assertIn('required_apps = ["frappe", "erpnext"]', hooks)
		self.assertNotIn("db_connector", hooks.split("required_apps", 1)[1].split("\n", 1)[0])
		self.assertIn("information_schema.COLUMNS", identity)
		self.assertIn("information_schema.STATISTICS", identity)
		self.assertIn("ccd_unified_person_current", identity)
		self.assertNotIn("fuzzy", identity.casefold())

	def test_independent_rollout_defaults_off_with_distinct_pilots(self):
		settings = json.loads(
			(DOCTYPE_ROOT / "ccd_portal_settings" / "ccd_portal_settings.json").read_text()
		)
		fields = {row["fieldname"]: row for row in settings["fields"]}
		self.assertEqual(fields["same_person_enabled"]["default"], "0")
		self.assertEqual(fields["same_person_pilot_users"]["options"], "CCD Portal Same Person Pilot User")
		self.assertNotEqual(fields["same_person_pilot_users"]["options"], fields["preview_users"]["options"])
		self.assertEqual(fields["service_name_fallback"]["default"], "Service not specified")
		self.assertIn("CCD database/source name", fields["service_name_fallback"]["options"])

	def test_settings_upgrade_backfills_new_required_limits(self):
		install = (ROOT / "ccd_portal" / "install.py").read_text()
		admin = (ROOT / "ccd_portal" / "admin.py").read_text()
		patches = (ROOT / "ccd_portal" / "patches.txt").read_text()
		self.assertIn("def ensure_settings_defaults()", install)
		self.assertIn('"concerns_per_hour": 10', install)
		self.assertIn("if cint(value) <= 0:", install)
		self.assertIn("ensure_settings_defaults()", admin)
		self.assertIn("backfill_settings_defaults", patches)

	def test_v2_policy_virtual_field_is_fixed_and_not_correctable(self):
		policy = json.loads((DOCTYPE_ROOT / "ccd_portal_policy" / "ccd_portal_policy.json").read_text())
		fields = json.loads(
			(DOCTYPE_ROOT / "ccd_portal_field_policy" / "ccd_portal_field_policy.json").read_text()
		)
		controller = (DOCTYPE_ROOT / "ccd_portal_policy" / "ccd_portal_policy.py").read_text()
		self.assertIn("same_person_enabled", {row["fieldname"] for row in policy["fields"]})
		source = next(row for row in fields["fields"] if row["fieldname"] == "source")
		self.assertIn("Unified Person", source["options"])
		for token in (
			'"fieldname": "unified_person_number"',
			'"mask_strategy": "Last 4"',
			'"correctable": 0',
			'"reveal_authorities": "Operator,Data Steward"',
		):
			self.assertIn(token, controller)

	def test_grouped_api_has_bounded_shape_and_fail_safe_fallback(self):
		api = (ROOT / "ccd_portal" / "api.py").read_text()
		for token in (
			'"person_id"',
			'"relationship_label"',
			'"person_fields"',
			'"direct_match"',
			'"record_count"',
			'"maximum_records"',
			"persons_for_records",
			"Identity integration is additive",
		):
			self.assertIn(token, api)
		self.assertIn('group.pop("_person", None)', api)
		self.assertNotIn('"ccd_source_key"', api)
		self.assertNotIn('"identity_group"', api)
		self.assertIn('"service_name"', api)

	def test_service_fallback_is_reasoned_audited_and_only_returns_a_display_label(self):
		admin = (ROOT / "ccd_portal" / "admin.py").read_text()
		security = (ROOT / "ccd_portal" / "security.py").read_text()
		panel = (ROOT / "frontend" / "src" / "components" / "AdminPanel.vue").read_text()
		app = (ROOT / "frontend" / "src" / "App.vue").read_text()
		self.assertIn("def set_service_name_fallback(fallback: str, reason: str)", admin)
		self.assertIn('require_system_manager(reason)', admin)
		self.assertIn('"Service Name Fallback Change"', admin)
		self.assertIn("record_service_name", security)
		self.assertIn("registration_service_name", security)
		self.assertIn("fallback_service_source", security)
		self.assertIn("saveServiceFallback", panel)
		self.assertIn("record.service_name", app)
		self.assertNotIn("record.fallback_service_source", app)

	def test_successful_identity_resolution_does_not_disable_expansion(self):
		tree = ast.parse((ROOT / "ccd_portal" / "api.py").read_text())
		grouped = next(
			node
			for node in tree.body
			if isinstance(node, ast.FunctionDef) and node.name == "_grouped_response"
		)
		disable_assignments = [
			node
			for node in ast.walk(grouped)
			if isinstance(node, ast.Assign)
			and any(isinstance(target, ast.Name) and target.id == "expand" for target in node.targets)
			and isinstance(node.value, ast.Constant)
			and node.value.value is False
		]
		self.assertEqual(len(disable_assignments), 1)
		identity_try = next(node for node in ast.walk(grouped) if isinstance(node, ast.Try))
		self.assertTrue(
			any(disable_assignments[0] in list(ast.walk(handler)) for handler in identity_try.handlers),
			"same-person expansion must only be disabled when identity resolution fails",
		)

	def test_concern_queue_encrypts_context_and_limits_reporter_visibility(self):
		api = (ROOT / "ccd_portal" / "api.py").read_text()
		concern = json.loads(
			(DOCTYPE_ROOT / "ccd_portal_identity_concern" / "ccd_portal_identity_concern.json").read_text()
		)
		fields = {row["fieldname"]: row for row in concern["fields"]}
		self.assertTrue(fields["encrypted_context"]["hidden"])
		self.assertTrue(fields["person_digest"]["hidden"])
		self.assertEqual(fields["status"]["options"], "Submitted\nIn Review\nResolved\nDismissed")
		self.assertIn('"encrypted_context": encrypt(', api)
		self.assertIn('filters={"reporter": context.user}', api)
		self.assertIn('"CCD Match Sensitive Reviewer"', json.dumps(concern))

	def test_browser_reveal_is_record_specific_and_ephemeral(self):
		app = (ROOT / "frontend" / "src" / "App.vue").read_text()
		for token in (
			"selected.value.id",
			"clearReveal",
			'visibilitychange',
			"onBeforeUnmount",
			"revealed.value?.person_fields",
		):
			self.assertIn(token, app)
		for storage in ("localStorage", "sessionStorage", "indexedDB"):
			self.assertNotIn(storage, app)

	def test_guarded_deployment_keeps_both_flags_off_and_checks_schema(self):
		remote = (ROOT / "deployment" / "deploy_remote.sh").read_text()
		development = (ROOT / "deployment" / "deploy_development.sh").read_text()
		for token in (
			"same_person_enabled",
			"ccd_portal.identity.schema_status",
			"Same-person rollout flag is enabled after migration",
		):
			self.assertIn(token, remote)
		self.assertIn("ccd_portal.identity.schema_status", development)


if __name__ == "__main__":
	unittest.main()
