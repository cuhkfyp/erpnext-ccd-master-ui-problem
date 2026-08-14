from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, now_datetime
from frappe.utils.password import decrypt

from ccd_portal.indexing import (
	coverage_report,
	deactivate_missing_records,
	index_record,
	mark_record_deleted,
	mark_source_identity_deleted,
	refresh_source,
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
	if frappe.db.count("CCD Portal Policy", {"status": "Active"}) != 1:
		return
	cutoff = add_days(now_datetime(), -2)
	for name in frappe.get_all("CCD Master", filters={"modified": [">=", cutoff]}, pluck="name"):
		try:
			index_record(name)
			reconcile_record(name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "CCD Portal scheduled reconciliation failure")
	try:
		deactivate_missing_records()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CCD Portal stale-index cleanup failure")


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
	source = str(source or "").strip()
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
