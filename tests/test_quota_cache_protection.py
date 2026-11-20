import ast
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import sys

ROOT = Path(__file__).resolve().parents[1]
scope = {}
exec((ROOT / "ccd_portal/hooks.py").read_text(), scope)
PREFIXES = scope["persistent_cache_keys"]


class QuotaCacheProtectionTests(TestCase):
	def test_every_current_quota_has_a_precise_persistent_prefix(self):
		tree = ast.parse((ROOT / "ccd_portal/api.py").read_text())
		operations = {
			n.args[1].value for n in ast.walk(tree)
			if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
			and n.func.id == "_check_rate_limit"
		}
		self.assertEqual(set(PREFIXES), {f"ccd-portal:{name}:" for name in operations})
		self.assertNotIn("ccd-portal:", PREFIXES)
		self.assertTrue(all(prefix.endswith(":") for prefix in PREFIXES))

	def test_preflight_invalidates_only_hooks_and_never_touches_counters(self):
		cache = Mock()
		fake = SimpleNamespace(cache=cache, get_hooks=Mock(return_value=PREFIXES))
		module = {}
		with patch.dict(sys.modules, {"frappe":fake, "ccd_portal.hooks":SimpleNamespace(persistent_cache_keys=PREFIXES)}):
			exec((ROOT / "ccd_portal/quota.py").read_text(), module)
			self.assertEqual(module["prepare_cache_protection"](), {"quota_cache_protection_ready":True})
		cache.delete_value.assert_called_once_with("app_hooks")
		self.assertEqual(len(cache.mock_calls), 1)

	def test_missing_preservation_hook_stops_preflight(self):
		fake = SimpleNamespace(cache=Mock(), get_hooks=Mock(return_value=PREFIXES[:-1]))
		module = {}
		with patch.dict(sys.modules, {"frappe":fake, "ccd_portal.hooks":SimpleNamespace(persistent_cache_keys=PREFIXES)}):
			exec((ROOT / "ccd_portal/quota.py").read_text(), module)
			with self.assertRaisesRegex(RuntimeError, "refusing cache maintenance"):
				module["prepare_cache_protection"]()

	def test_all_deployment_paths_prepare_before_cache_clear_or_migration(self):
		for script in ["deploy_development.sh", "deploy_remote.sh", "recover_remote_runtime.sh"]:
			text = (ROOT / "deployment" / script).read_text()
			preflight = text.index("ccd_portal.quota.prepare_cache_protection")
			self.assertLess(preflight, text.index('" clear-cache'))
			if script != "recover_remote_runtime.sh":
				self.assertLess(preflight, text.index('" migrate'))
