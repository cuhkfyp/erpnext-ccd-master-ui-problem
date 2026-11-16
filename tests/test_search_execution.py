"""Exercise the production search function with isolated DB and framework boundaries."""
import ast
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class ProcessingLimit(Exception):
	pass


def criterion(field="chi_firstname", value="SYNTHETIC", *, alternatives=None, strong=False):
	return dict(source="CCD Master", fieldname=field, normalized=value, strong=strong,
		data_kind="Text", token_fieldnames=alternatives or (field,))


class SearchExecutionTests(unittest.TestCase):
	def setUp(self):
		self.messages = []
		def throw(message, exception):
			self.messages.append(message)
			raise exception(message)
		self.db = SimpleNamespace(sql=Mock(return_value=[]), commit=Mock())
		self.audit = Mock()
		self.rate_limit = Mock()
		self.context = SimpleNamespace(is_break_glass=False, centres=("SYNTHETIC-CENTRE",))
		self.scope = {
			"PortalContext": object, "time": time, "_": lambda message: message,
			"frappe": SimpleNamespace(db=self.db, throw=throw, get_single=lambda _: SimpleNamespace(searches_per_hour=60, chinese_first_name_searches_per_hour=10)),
			"mark_search_activity": Mock(), "_check_rate_limit": self.rate_limit,
			"policy_field_map": lambda: ({"policy_version":"V2"}, {}),
			"_search_criteria": lambda criteria, fields: criteria,
			"is_restricted_name_search": lambda criteria: True,
			"get_active_policy": lambda: ({"policy_version":"V2"}, []),
			"_same_person_state": lambda *args: {"effective":False},
			"search_token": lambda field, value: "TOKEN:" + field + ":" + value,
			"nowdate": lambda: "2000-01-01", "MAX_RESULTS":20,
			"SEARCH_QUERY_TIMEOUT_SECONDS":8, "SEARCH_QUERY_RETRY_TIMEOUT_SECONDS":4,
			"is_statement_timeout_error": lambda exc: isinstance(exc, TimeoutError),
			"write_event": self.audit, "SearchProcessingLimitError":ProcessingLimit,
			"SERVICE_FALLBACK_UNSPECIFIED":"Service not specified",
			"_grouped_response": lambda *args, **kwargs: {"record_count":0,"truncated":False},
			"_common_person_service_names": lambda *args, **kwargs: [],
		}
		tree = ast.parse((Path(__file__).resolve().parents[1] / "ccd_portal/api.py").read_text())
		function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_search")
		exec(compile(ast.Module(body=[function], type_ignores=[]), "api.py", "exec"), self.scope)
		self.search = self.scope["_search"]

	def test_two_timeouts_audit_error_and_publish_a_user_message(self):
		self.db.sql.side_effect = TimeoutError("database limit")
		with self.assertRaises(ProcessingLimit):
			self.search(self.context, [criterion()])
		self.assertEqual(self.db.sql.call_count, 2)
		self.assertEqual(len(self.messages), 1)
		self.assertIn("Search processing limit reached", self.messages[0])
		self.assertEqual(self.audit.call_args.kwargs["outcome"], "Error")
		self.assertEqual(self.audit.call_args.kwargs["metadata"]["attempts"], 2)
		self.db.commit.assert_called_once()

	def test_retry_preserves_the_query_and_counts_one_search(self):
		self.db.sql.side_effect = [TimeoutError(), []]
		self.search(self.context, [criterion()])
		first, second = self.db.sql.call_args_list
		self.assertIn("max_statement_time=8", first.args[0])
		self.assertIn("max_statement_time=4", second.args[0])
		self.assertEqual(first.args[0].split("FOR",1)[1], second.args[0].split("FOR",1)[1])
		self.assertEqual(first.args[1], second.args[1])
		self.assertEqual(self.rate_limit.call_count, 2)  # Once each for search and name budgets.
		self.assertEqual(self.messages, [])
		self.audit.assert_called_once()

	def test_unexpected_database_failure_is_not_retried_or_mislabelled(self):
		self.db.sql.side_effect = RuntimeError("unexpected failure")
		with self.assertRaises(RuntimeError):
			self.search(self.context, [criterion()])
		self.db.sql.assert_called_once()
		self.assertEqual(self.messages, [])
		self.audit.assert_not_called()

	def test_phone_or_and_name_filters_bind_in_sql_order_with_centre_scope(self):
		self.search(self.context, [criterion(), criterion("phone_number", "12345678", alternatives=("mobile", "residential"), strong=True)])
		query, values = self.db.sql.call_args.args
		self.assertEqual(query.count("%s"), len(values))
		self.assertEqual(values, ["V2", "chi_firstname", "TOKEN:chi_firstname:SYNTHETIC",
			"V2", "mobile", "TOKEN:mobile:12345678", "residential", "TOKEN:residential:12345678",
			"2000-01-01", "2000-01-01", "SYNTHETIC-CENTRE", 21])
		self.assertLess(query.index("Search Token` t2"), query.index("Portal Record` r"))
		self.assertIn("rc.centre IN (%s)", query)
		self.assertIn("COALESCE(sp.sync_pending, 0) = 0", query)
