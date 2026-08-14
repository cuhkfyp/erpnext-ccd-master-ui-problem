import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
	def test_all_doctype_json_is_valid_and_not_exportable(self):
		for path in (ROOT / "ccd_portal" / "ccd_portal" / "doctype").glob("*/*.json"):
			doc = json.loads(path.read_text(encoding="utf-8"))
			self.assertEqual(doc["doctype"], "DocType")
			for permission in doc.get("permissions", []):
				self.assertFalse(permission.get("export"), f"export enabled in {path}")
				self.assertFalse(permission.get("print"), f"print enabled in {path}")

	def test_portal_api_does_not_return_source_identity_names(self):
		api = (ROOT / "ccd_portal" / "api.py").read_text(encoding="utf-8")
		self.assertNotIn('"ccd_source_key"', api)
		self.assertNotIn('"ccd_reg_source"', api)
		self.assertNotIn("match_table", api)
		self.assertIn("MAX_RESULTS = 20", api)

	def test_feature_flag_defaults_off(self):
		settings = json.loads(
			(ROOT / "ccd_portal" / "ccd_portal" / "doctype" / "ccd_portal_settings" / "ccd_portal_settings.json").read_text()
		)
		enabled = next(field for field in settings["fields"] if field["fieldname"] == "enabled")
		self.assertEqual(enabled["default"], "0")

	def test_no_automatic_audit_retention_hook(self):
		hooks = (ROOT / "ccd_portal" / "hooks.py").read_text(encoding="utf-8")
		self.assertNotIn("default_log_clearing_doctypes", hooks)
		audit = (ROOT / "ccd_portal" / "audit.py").read_text(encoding="utf-8")
		self.assertIn("frappe.db.commit()", audit)

	def test_app_permission_hook_resolves_to_security_module(self):
		hooks = (ROOT / "ccd_portal" / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('"has_permission": "ccd_portal.security.has_portal_permission"', hooks)
		self.assertIn('after_migrate = "ccd_portal.install.after_migrate"', hooks)
		self.assertNotIn("ccd_portal.auth", hooks)

	def test_registration_mapper_exposes_centre_key_idempotently(self):
		patch = (
			ROOT / "ccd_portal" / "patches" / "v1_0" / "create_ccd_master_centre_field.py"
		).read_text(encoding="utf-8")
		self.assertIn('MAPPING_OPTION = "ccd_portal_centre_key:', patch)
		self.assertIn('get_meta("CCD Field Match").get_field("sys_fieldname")', patch)
		self.assertIn('option.partition(":")[0].strip() == "ccd_portal_centre_key"', patch)
		self.assertIn("make_property_setter(", patch)
		self.assertIn("validate_fields_for_doctype=False", patch)

	def test_raw_sync_adapter_is_server_side_only(self):
		sync = (ROOT / "ccd_portal" / "sync.py").read_text(encoding="utf-8")
		indexing = (ROOT / "ccd_portal" / "indexing.py").read_text(encoding="utf-8")
		adapter_offset = sync.index("def after_agent_sync(")
		decorator_window = sync[max(0, adapter_offset - 100) : adapter_offset]
		self.assertNotIn("whitelist", decorator_window)
		self.assertIn("deactivate_missing_records", sync)
		self.assertIn('"duplicate_source_identity"', indexing)
		self.assertIn("_deactivate_portal_record(stale_name)", indexing)

	def test_frontend_does_not_use_browser_persistent_storage(self):
		source = "\n".join(
			path.read_text(encoding="utf-8") for path in (ROOT / "frontend" / "src").rglob("*.vue")
		)
		source += "\n" + "\n".join(
			path.read_text(encoding="utf-8") for path in (ROOT / "frontend" / "src").rglob("*.js")
		)
		for browser_store in ("localStorage", "sessionStorage", "indexedDB"):
			self.assertNotIn(browser_store, source)

	def test_login_action_distinguishes_guest_from_denied_user(self):
		page = (ROOT / "ccd_portal" / "www" / "ccd_portal.py").read_text(encoding="utf-8")
		app = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
		self.assertIn('"ccd_portal_authenticated": frappe.session.user != "Guest"', page)
		self.assertIn("Boolean(window.ccd_portal_authenticated)", app)
		self.assertIn("!sessionAuthenticated && (e.status===401 || e.status===403)", app)
		self.assertIn('/login?redirect-to=%2Fccd-portal', app)
		self.assertIn('href="/app"', app)

	def test_admin_forms_use_guided_references_and_parser_fields(self):
		panel = (ROOT / "frontend" / "src" / "components" / "AdminPanel.vue").read_text(encoding="utf-8")
		admin = (ROOT / "ccd_portal" / "admin.py").read_text(encoding="utf-8")
		style = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")
		self.assertIn('reference:"centres"', panel)
		self.assertIn('reference:"profiles"', panel)
		self.assertIn('showWhen:{parser_type:"Regular Expression"}', panel)
		self.assertIn("Enter a bounded pattern, or choose Exact", panel)
		self.assertIn("def reference_options():", admin)
		self.assertIn('"CCD Registration"', admin)
		self.assertIn('.field input[type="checkbox"]', style)
		self.assertIn("appearance:auto", style)
		self.assertIn("-webkit-appearance:checkbox", style)

	def test_built_assets_have_no_source_maps(self):
		assets = ROOT / "ccd_portal" / "public" / "ccd-portal" / "assets"
		self.assertEqual(list(assets.glob("*.map")), [])
		self.assertEqual(len(list(assets.glob("*.js"))), 1)
		self.assertEqual(len(list(assets.glob("*.css"))), 1)

	def test_deploy_is_persistent_and_restart_is_opt_in(self):
		deploy = (ROOT / "deployment" / "deploy_development.sh").read_text(encoding="utf-8")
		self.assertIn('PERSISTENT_APP="$VOLUME_ROOT/persistent_apps/$APP_NAME"', deploy)
		self.assertIn("if (( RESTART )); then", deploy)
		self.assertIn("docker kill --signal=HUP frappe_docker-backend-1", deploy)
		self.assertIn("Gunicorn web workers gracefully reloaded", deploy)
		self.assertIn("workers_reloaded", deploy)
		self.assertIn("--runtime-only", deploy)
		self.assertIn("--persist-only", deploy)
		self.assertIn("without touching Docker or the site", deploy)
		self.assertIn("sshmount_docker_backend.sh", deploy)
		self.assertIn('$APP_IN_CONTAINER/$APP_NAME/public/.', deploy)
		self.assertIn('mktemp -d "$VOLUME_ROOT/.ccd-portal-assets.', deploy)
		self.assertIn("ccd_portal.pth", deploy)
		self.assertNotIn("pip install", deploy)
		self.assertIn('awk "1" "$file"', deploy)

	def test_sshmount_recovery_is_layer_aware_and_bounded(self):
		script = (ROOT / "deployment" / "sshmount_docker_backend.sh").read_text(encoding="utf-8")
		self.assertIn("mapfile -t mounted_sources", script)
		self.assertIn("visible layer(s)); no remount performed", script)
		self.assertIn("for _attempt in {1..16}", script)
		self.assertIn("after_count >= before_count", script)
		self.assertIn("Refusing to unmount non-SSHFS layer", script)
