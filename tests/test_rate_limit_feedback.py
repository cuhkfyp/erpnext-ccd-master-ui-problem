"""Quota feedback must not reset the user's Redis window or change the budget."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock


class RateLimitFeedbackTests(TestCase):
	def setUp(self):
		self.cache = Mock()
		self.cache.make_key.return_value = "test-user-reveal"
		self.cache.incrby.return_value = 51
		self.cache.ttl.return_value = 1182
		self.response = {}
		self.throw = Mock(side_effect=ValueError("denied"))
		self.audit = Mock()
		self.scope = {
			"PortalContext": object, "cint": int, "_": lambda value: value,
			"audit_denial": self.audit,
			"frappe": SimpleNamespace(cache=self.cache, local=SimpleNamespace(response=self.response),
				throw=self.throw, RateLimitExceededError=ValueError),
		}
		tree = ast.parse((Path(__file__).resolve().parents[1] / "ccd_portal/api.py").read_text())
		function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_check_rate_limit")
		exec(compile(ast.Module(body=[function], type_ignores=[]), "api.py", "exec"), self.scope)

	def check(self, operation="reveal"):
		self.scope["_check_rate_limit"](SimpleNamespace(user="synthetic-user"), operation, 50)

	def test_denial_reports_existing_ttl_without_extending_window(self):
		with self.assertRaises(ValueError):
			self.check()
		self.assertEqual(self.response, {"retry_after_seconds": 1183})
		self.assertIn("50 requests per hour", self.throw.call_args.args[0])
		self.cache.set.assert_called_once_with("test-user-reveal", 0, ex=3600, nx=True)
		self.cache.expire.assert_not_called()
		self.cache.incrby.assert_called_once_with("test-user-reveal", 1)
		self.audit.assert_called_once()

	def test_allowed_request_has_no_retry_metadata(self):
		self.cache.incrby.return_value = 50
		self.check()
		self.assertEqual(self.response, {})
		self.cache.ttl.assert_not_called()
		self.throw.assert_not_called()

	def test_expired_key_does_not_invent_a_reset_time(self):
		self.cache.ttl.return_value = -2
		with self.assertRaises(ValueError):
			self.check()
		self.assertEqual(self.response, {})

	def test_other_operations_keep_their_generic_limit_message(self):
		with self.assertRaises(ValueError):
			self.check("search")
		self.assertEqual(self.throw.call_args.args[0], "Too many requests. Try again later.")
