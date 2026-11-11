from __future__ import annotations

import json
import time

import frappe
from frappe.utils import add_days, get_datetime, now_datetime
from frappe.utils.password import decrypt

from ccd_portal.indexing import (
	coverage_report,
	deactivate_missing_records,
	index_record,
	mark_record_deleted,
	mark_source_identity_deleted,
	refresh_source,
)
from ccd_portal.primitives import is_transient_database_lock_error
from ccd_portal.source_identity import stable_source_id
from ccd_portal.workload import search_recently_active


RECONCILIATION_CURSOR_KEY = "ccd_portal:recent_reconciliation_cursor"
RECONCILIATION_MAX_RECORDS = 250
RECONCILIATION_COMMIT_SIZE = 10
RECONCILIATION_TIME_BUDGET_SECONDS = 20
RECONCILIATION_CURSOR_TTL_SECONDS = 14 * 24 * 60 * 60


def _source_refresh_in_progress() -> bool:
	return bool(
		frappe.db.exists("DocType", "CCD Portal Source Refresh")
		and frappe.db.exists(
			"CCD Portal Source Refresh",
			{"status": ["in", ["Queued", "Running"]]},
		)
	)


def _reconciliation_cursor() -> dict:
	state = frappe.cache.get_value(RECONCILIATION_CURSOR_KEY, expires=True)
	if isinstance(state, bytes):
		try:
			state = json.loads(state.decode("utf-8"))
		except (TypeError, ValueError):
			state = None
	if isinstance(state, str):
		try:
			state = json.loads(state)
		except (TypeError, ValueError):
			state = None
	if isinstance(state, dict) and all(
		state.get(field)
		for field in ("window_start", "window_end", "last_modified")
	):
		return state
	now = now_datetime()
	return {
		"window_start": str(add_days(now, -2)),
		"window_end": str(now),
		"last_modified": str(add_days(now, -2)),
		"last_name": "",
	}


def _save_reconciliation_cursor(state: dict) -> None:
	frappe.cache.set_value(
		RECONCILIATION_CURSOR_KEY,
		json.dumps(state, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
		expires_in_sec=RECONCILIATION_CURSOR_TTL_SECONDS,
	)


def on_ccd_master_change(doc, method=None) -> None:
	if not frappe.conf.get("ccd_portal_hmac_secret"):
		return
	index_record(doc.name)
	reconcile_record(doc.name)


def on_ccd_master_delete(doc, method=None) -> None:
	if not frappe.conf.get("ccd_portal_hmac_secret"):
		return
	mark_record_deleted(doc)


def reconcile_record(ccd_master_name: str) -> None:
	record = frappe.db.get_value(
		"CCD Portal Record", {"ccd_master_record": ccd_master_name, "active": 1}, ["name"], as_dict=True
	)
	if not record:
		return
	doc = frappe.get_doc("CCD Master", ccd_master_name)
	requests = frappe.get_all(
		"CCD Portal Correction Request",
		filters={"portal_record": record.name, "status": "Approved"},
		fields=["name", "encrypted_changes", "approved_source_modified"],
	)
	for request in requests:
		try:
			changes = json.loads(decrypt(request.encrypted_changes))
		except Exception:
			frappe.log_error(frappe.get_traceback(), "CCD Portal correction decryption failure")
			continue
		if all(str(doc.get(field) or "") == str(value or "") for field, value in changes.items()):
			status = "Applied"
		elif request.approved_source_modified and doc.modified > request.approved_source_modified:
			status = "Needs Review"
		else:
			continue
		frappe.db.set_value(
			"CCD Portal Correction Request",
			request.name,
			{"status": status, "reconciled_on": now_datetime()},
			update_modified=False,
		)


def reconcile_recent_records() -> None:
	if not frappe.conf.get("ccd_portal_hmac_secret"):
		return
	policies = frappe.get_all(
		"CCD Portal Policy",
		filters={"status": "Active"},
		fields=["policy_version"],
		limit=2,
	)
	if len(policies) != 1:
		return
	# This hourly safety net must never compete with a governed full-source
	# refresh. Document events remain the primary real-time indexing path.
	if _source_refresh_in_progress() or search_recently_active():
		return
	started = time.monotonic()
	state = _reconciliation_cursor()
	rows = frappe.db.sql(
		"""
		SELECT m.name, m.modified
		  FROM `tabCCD Master` m
		 WHERE m.modified >= %s
		   AND m.modified <= %s
		   AND (m.modified > %s OR (m.modified = %s AND m.name > %s))
		   AND NOT EXISTS (
		       SELECT 1
		         FROM `tabCCD Portal Record` r
		        WHERE r.ccd_master_record = m.name
		          AND r.active = 1
		          AND r.policy_version = %s
		          AND r.source_modified >= m.modified
		   )
		 ORDER BY m.modified, m.name
		 LIMIT %s
		""",
		(
			get_datetime(state["window_start"]),
			get_datetime(state["window_end"]),
			get_datetime(state["last_modified"]),
			get_datetime(state["last_modified"]),
			str(state.get("last_name") or ""),
			policies[0].policy_version,
			RECONCILIATION_MAX_RECORDS,
		),
		as_dict=True,
	)
	committed_state = dict(state)
	for position, row in enumerate(rows):
		if (
			search_recently_active()
			or _source_refresh_in_progress()
			or time.monotonic() - started >= RECONCILIATION_TIME_BUDGET_SECONDS
		):
			frappe.db.commit()
			_save_reconciliation_cursor(committed_state)
			return
		save_point = f"ccd_portal_reconcile_{position}"
		frappe.db.savepoint(save_point)
		try:
			index_record(row.name)
			reconcile_record(row.name)
		except Exception as exc:
			traceback = frappe.get_traceback()
			if is_transient_database_lock_error(exc):
				# A deadlock may discard all savepoints. Replay only the current
				# small transaction batch during the next hourly safety-net run.
				frappe.db.rollback()
				_save_reconciliation_cursor(committed_state)
				frappe.log_error(traceback, "CCD Portal scheduled reconciliation deferred")
				return
			frappe.db.rollback(save_point=save_point)
			frappe.db.release_savepoint(save_point)
			frappe.log_error(traceback, "CCD Portal scheduled reconciliation failure")
		else:
			frappe.db.release_savepoint(save_point)
		if (position + 1) % RECONCILIATION_COMMIT_SIZE == 0:
			frappe.db.commit()
			committed_state.update(
				{"last_modified": str(row.modified), "last_name": row.name}
			)
			_save_reconciliation_cursor(committed_state)
			if (
				_source_refresh_in_progress()
				or search_recently_active()
				or time.monotonic() - started >= RECONCILIATION_TIME_BUDGET_SECONDS
			):
				return
	if rows and len(rows) % RECONCILIATION_COMMIT_SIZE:
		frappe.db.commit()
		last = rows[-1]
		committed_state.update(
			{"last_modified": str(last.modified), "last_name": last.name}
		)
		_save_reconciliation_cursor(committed_state)
	if len(rows) >= RECONCILIATION_MAX_RECORDS:
		return
	# Retain a high-water mark instead of scanning the same two-day window every
	# hour. The next run begins at this window boundary; the inclusive comparison
	# and stale-record predicate make the handoff idempotent.
	next_start = get_datetime(state["window_end"])
	_save_reconciliation_cursor(
		{
			"window_start": str(next_start),
			"window_end": str(now_datetime()),
			"last_modified": str(next_start),
			"last_name": "",
		}
	)


def after_agent_sync(
	source: str,
	source_keys: list[str] | None = None,
	deleted_source_keys: list[str] | None = None,
	full_sync: bool = False,
) -> dict:
	"""Trusted post-sync adapter for integrations that bypass Frappe document events.

	This method is deliberately not whitelisted. The existing agent API may call it
	server-side after its own authorization and successful source transaction.
	"""
	if not frappe.conf.get("ccd_portal_hmac_secret"):
		return {"status": "disabled", "reason": "secret_not_configured"}
	if frappe.db.count("CCD Portal Policy", {"status": "Active"}) != 1:
		return {"status": "disabled", "reason": "active_policy_not_configured"}
	source = stable_source_id(source)
	if not source:
		frappe.throw("A source registration is required.", frappe.ValidationError)
	source_keys = sorted({str(key) for key in (source_keys or []) if str(key)})
	deleted_source_keys = sorted({str(key) for key in (deleted_source_keys or []) if str(key)})
	if len(source_keys) > 10000 or len(deleted_source_keys) > 10000:
		frappe.throw("The synchronization batch is too large.", frappe.ValidationError)
	refresh = (
		refresh_source(source, None if full_sync else source_keys)
		if full_sync or source_keys
		else {"total": 0, "indexed": 0, "unmapped": 0, "failed": 0, "deactivated": 0}
	)
	deactivated = sum(mark_source_identity_deleted(source, key) for key in deleted_source_keys)
	if full_sync:
		deactivated += deactivate_missing_records(source)
	return {"status": "ok", "refresh": refresh, "deactivated": deactivated}


def generate_coverage_snapshot() -> None:
	if not frappe.conf.get("ccd_portal_hmac_secret"):
		return
	report = coverage_report()
	frappe.cache.set_value("ccd_portal:last_coverage", report, expires_in_sec=48 * 60 * 60)
