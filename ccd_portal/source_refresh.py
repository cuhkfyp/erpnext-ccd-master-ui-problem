from __future__ import annotations

import hashlib
import json
import uuid

import frappe
from frappe import _
from frappe.utils import now_datetime

from ccd_portal.audit import audit_denial, write_event
from ccd_portal.indexing import index_record, source_index_context
from ccd_portal.policy import get_active_policy
from ccd_portal.security import (
	PortalContext,
	clean_reason,
	require_access_administrator,
	require_post,
	require_system_manager,
)
from ccd_portal.source_identity import canonical_source_id


OPERATION_TTL_SECONDS = 7 * 24 * 60 * 60
JOB_TIMEOUT_SECONDS = 6 * 60 * 60
PAGE_SIZE = 250
ACTIVE_STATUSES = {"Queued", "Running"}
FAILED_JOB_STATUSES = {"failed", "canceled", "stopped"}


class SourceRefreshBlocked(RuntimeError):
	def __init__(self, reason: str):
		self.reason = str(reason or "index_validation")[:80]
		super().__init__(self.reason)


def _json(value) -> str:
	return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=str)


def _operation_key(operation_token: str) -> str:
	return f"ccd_portal_source_refresh:{operation_token}"


def _active_key(source: str) -> str:
	digest = hashlib.sha256(canonical_source_id(source).encode("utf-8")).hexdigest()[:32]
	return f"ccd_portal_source_refresh_active:{digest}"


def _operation(operation_token: str) -> dict | None:
	raw = frappe.cache.get_value(_operation_key(operation_token), expires=True)
	if isinstance(raw, bytes):
		raw = raw.decode("utf-8", "replace")
	try:
		payload = json.loads(raw) if raw else None
	except (TypeError, ValueError):
		return None
	return payload if isinstance(payload, dict) else None


def _set_operation(operation_token: str, payload: dict) -> None:
	frappe.cache.set_value(
		_operation_key(operation_token),
		_json(payload),
		expires_in_sec=OPERATION_TTL_SECONDS,
	)


def _active_operation(source: str) -> dict | None:
	token = frappe.cache.get_value(_active_key(source))
	if isinstance(token, bytes):
		token = token.decode("utf-8", "replace")
	return _operation(str(token)) if token else None


def assert_source_refresh_available(source: str) -> None:
	existing = _active_operation(source)
	if existing and existing.get("status") in ACTIVE_STATUSES:
		frappe.throw(
			_("A background refresh is already running for this source. Wait for it to finish before changing the configuration."),
			frappe.ValidationError,
		)


def operation_for_source(source: str, viewer: str) -> dict:
	payload = _active_operation(source)
	if not payload:
		return {}
	result = {
		"refresh_status": payload.get("status"),
		"refresh_stage": payload.get("stage"),
		"refresh_processed": int(payload.get("processed_records") or 0),
		"refresh_total": int(payload.get("total_records") or 0),
		"refresh_in_progress": payload.get("status") in ACTIVE_STATUSES,
	}
	if payload.get("requested_by") == viewer:
		result["refresh_operation_token"] = payload.get("operation_token")
	return result


def disable_source_relations(source: str) -> None:
	"""Remove the access path immediately; the profile gate is a second defence."""
	source_id = canonical_source_id(source)
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Record Centre` rc
		JOIN `tabCCD Portal Record` r ON r.name = rc.portal_record
		   SET rc.active = 0
		 WHERE r.source_registration = %s AND rc.active = 1
		""",
		(source_id,),
	)


def queue_source_refresh(
	*,
	profile_name: str,
	source: str,
	reason: str,
	context: PortalContext,
	completion_event: str = "Index Refresh",
) -> dict:
	"""Commit a fail-closed source gate and enqueue the bounded long worker."""
	reason = clean_reason(reason)
	source_id = canonical_source_id(source)
	locked = frappe.db.sql(
		"SELECT name FROM `tabCCD Portal Source Profile` WHERE name=%s FOR UPDATE",
		(profile_name,),
	)
	if not locked:
		frappe.throw(_("Select an active source assignment."), frappe.ValidationError)
	existing = _active_operation(source_id)
	if existing and existing.get("status") in ACTIVE_STATUSES:
		frappe.throw(_("A background refresh is already running for this source."), frappe.ValidationError)

	profile = frappe.db.get_value(
		"CCD Portal Source Profile",
		profile_name,
		["name", "source_registration", "canonical_source_id", "active"],
		as_dict=True,
	)
	if not profile or not profile.active or canonical_source_id(profile.source_registration) != source_id:
		frappe.throw(_("Select an active source assignment."), frappe.ValidationError)

	frappe.db.set_value("CCD Portal Source Profile", profile.name, "sync_pending", 1)
	disable_source_relations(source_id)
	profile_modified = frappe.db.get_value("CCD Portal Source Profile", profile.name, "modified")
	total = int(frappe.db.count("CCD Master", {"ccd_reg_source": source_id}) or 0)
	operation_token = uuid.uuid4().hex
	payload = {
		"operation_token": operation_token,
		"profile_name": profile.name,
		"profile_modified": str(profile_modified),
		"source": source_id,
		"requested_by": context.user,
		"status": "Queued",
		"stage": "queued",
		"processed_records": 0,
		"total_records": total,
		"queued_at": str(now_datetime()),
		"completion_event": completion_event,
	}
	_set_operation(operation_token, payload)
	frappe.cache.set_value(
		_active_key(source_id),
		operation_token,
		expires_in_sec=OPERATION_TTL_SECONDS,
	)
	try:
		write_event(
			"Index Refresh Queued",
			context=context,
			protected_value={"source": source_id, "reason": reason},
			metadata={
				"operation_token": operation_token,
				"total_records": total,
				"background": True,
			},
		)
		frappe.enqueue(
			"ccd_portal.source_refresh.run_source_refresh",
			queue="long",
			timeout=JOB_TIMEOUT_SECONDS,
			enqueue_after_commit=True,
			job_id=f"ccd-portal-source-refresh-{operation_token}",
			operation_token=operation_token,
			profile_name=profile.name,
			source=source_id,
			requested_by=context.user,
		)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.cache.delete_value(_active_key(source_id))
		frappe.cache.delete_value(_operation_key(operation_token))
		raise
	return {"operation_token": operation_token, "status": "Queued", "total_records": total}


def _source_snapshot(source: str) -> dict:
	row = frappe.db.sql(
		"""
		SELECT COUNT(*) AS total, MAX(modified) AS latest_modified
		  FROM `tabCCD Master`
		 WHERE ccd_reg_source = %s
		""",
		(source,),
		as_dict=True,
	)[0]
	return {"total": int(row.total or 0), "latest_modified": str(row.latest_modified or "")}


def _governance_snapshot(profile_name: str) -> dict:
	centre = frappe.db.sql(
		"SELECT COUNT(*) AS total, MAX(modified) AS latest_modified FROM `tabCCD Portal Centre`",
		as_dict=True,
	)[0]
	alias = frappe.db.sql(
		"""
		SELECT COUNT(*) AS total, MAX(modified) AS latest_modified
		  FROM `tabCCD Portal Centre Alias`
		 WHERE source_profile IS NULL OR source_profile='' OR source_profile=%s
		""",
		(profile_name,),
		as_dict=True,
	)[0]
	return {
		"centres": (int(centre.total or 0), str(centre.latest_modified or "")),
		"aliases": (int(alias.total or 0), str(alias.latest_modified or "")),
	}


def _stage_source_index(source: str) -> None:
	frappe.db.sql(
		"UPDATE `tabCCD Portal Record` SET active=0 WHERE source_registration=%s",
		(source,),
	)
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Search Token` t
		JOIN `tabCCD Portal Record` r ON r.name=t.portal_record
		   SET t.active=0
		 WHERE r.source_registration=%s
		""",
		(source,),
	)
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Record Centre` rc
		JOIN `tabCCD Portal Record` r ON r.name=rc.portal_record
		   SET rc.active=0
		 WHERE r.source_registration=%s
		""",
		(source,),
	)
	frappe.db.commit()


def _publish_source_index(source: str) -> None:
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Record` r
		LEFT JOIN `tabCCD Master` m ON m.name=r.ccd_master_record
		   SET r.active=CASE
		       WHEN m.name IS NOT NULL AND m.ccd_reg_source=%s THEN 1
		       ELSE 0
		   END
		 WHERE r.source_registration=%s
		""",
		(source, source),
	)
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Search Token` t
		JOIN `tabCCD Portal Record` r ON r.name=t.portal_record
		   SET t.active=r.active
		 WHERE r.source_registration=%s
		""",
		(source,),
	)
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Record Centre` rc
		JOIN `tabCCD Portal Record` r ON r.name=rc.portal_record
		   SET rc.active=r.active
		 WHERE r.source_registration=%s
		""",
		(source,),
	)


def _fail_operation(operation_token: str, payload: dict, reason: str) -> None:
	payload.update(
		{
			"status": "Failed",
			"stage": "failed",
			"completed_at": str(now_datetime()),
			"error": _(
				"The background index refresh failed. The source remains disabled; review the server error log before retrying."
			),
			"blocked_reason": str(reason or "index_failure")[:80],
		}
	)
	_set_operation(operation_token, payload)


def run_source_refresh(
	operation_token: str,
	profile_name: str,
	source: str,
	requested_by: str,
) -> dict:
	"""Build inactive pages, then publish the complete source in one short transaction."""
	if "System Manager" not in set(frappe.get_roles(requested_by)):
		raise frappe.PermissionError("System Manager role is required")
	frappe.set_user(requested_by)
	payload = _operation(operation_token)
	if not payload or payload.get("requested_by") != requested_by:
		raise frappe.PermissionError("Source refresh operation is unavailable")
	payload.update(
		{
			"status": "Running",
			"stage": "staging",
			"started_at": str(now_datetime()),
		}
	)
	_set_operation(operation_token, payload)
	context = PortalContext(user=requested_by, authority="System Manager", centres=(), is_break_glass=True)
	try:
		profile = frappe.db.get_value(
			"CCD Portal Source Profile",
			profile_name,
			["name", "canonical_source_id", "sync_pending", "active", "modified"],
			as_dict=True,
		)
		if (
			not profile
			or not profile.active
			or canonical_source_id(profile.canonical_source_id) != canonical_source_id(source)
			or not profile.sync_pending
			or str(profile.modified) != str(payload.get("profile_modified"))
		):
			raise SourceRefreshBlocked("source_profile_changed")

		_stage_source_index(source)
		snapshot = _source_snapshot(source)
		governance_snapshot = _governance_snapshot(profile_name)
		payload.update(
			{
				"stage": "indexing",
				"processed_records": 0,
				"total_records": snapshot["total"],
			}
		)
		_set_operation(operation_token, payload)
		index_context = source_index_context(source, allow_sync_pending=True)
		if not index_context.get("profile"):
			raise SourceRefreshBlocked("source_profile_missing")
		policy_version = index_context["policy"]["policy_version"]
		processed = 0
		last_name = ""
		page_number = 0
		while True:
			names = frappe.db.sql(
				"""
				SELECT name
				  FROM `tabCCD Master`
				 WHERE ccd_reg_source=%s AND name>%s
				 ORDER BY name
				 LIMIT %s
				""",
				(source, last_name, PAGE_SIZE),
				pluck=True,
			)
			if not names:
				break
			page_number += 1
			for position, name in enumerate(names):
				save_point = f"ccd_portal_source_{page_number}_{position}"
				frappe.db.savepoint(save_point)
				try:
					result = index_record(
						name,
						allow_sync_pending=True,
						publish=False,
						index_context=index_context,
					)
					if result.get("status") != "indexed":
						raise SourceRefreshBlocked(result.get("reason") or "record_unmapped")
				except Exception:
					frappe.db.rollback(save_point=save_point)
					raise
				else:
					frappe.db.release_savepoint(save_point)
				processed += 1
			last_name = names[-1]
			frappe.db.commit()
			payload.update({"processed_records": processed, "stage": "indexing"})
			_set_operation(operation_token, payload)

		if _source_snapshot(source) != snapshot:
			raise SourceRefreshBlocked("source_changed_during_refresh")
		if _governance_snapshot(profile_name) != governance_snapshot:
			raise SourceRefreshBlocked("centre_governance_changed_during_refresh")
		current_profile = frappe.db.get_value(
			"CCD Portal Source Profile",
			profile_name,
			["modified", "sync_pending", "active"],
			as_dict=True,
		)
		if (
			not current_profile
			or not current_profile.active
			or not current_profile.sync_pending
			or str(current_profile.modified) != str(payload.get("profile_modified"))
		):
			raise SourceRefreshBlocked("source_profile_changed")
		current_policy, _ = get_active_policy()
		if current_policy["policy_version"] != policy_version:
			raise SourceRefreshBlocked("policy_changed_during_refresh")

		payload.update({"stage": "publishing", "processed_records": processed})
		_set_operation(operation_token, payload)
		_publish_source_index(source)
		frappe.db.set_value("CCD Portal Source Profile", profile_name, "sync_pending", 0)
		metadata = {
			"operation_token": operation_token,
			"total": snapshot["total"],
			"indexed": processed,
			"unmapped": 0,
			"failed": 0,
			"background": True,
			"strict_source_gate": True,
		}
		write_event(
			"Index Refresh",
			context=context,
			protected_value={"source": source},
			metadata=metadata,
		)
		completion_event = str(payload.get("completion_event") or "Index Refresh")
		if completion_event != "Index Refresh":
			write_event(
				completion_event,
				context=context,
				protected_value={"source": source},
				metadata=metadata,
			)
		frappe.db.commit()
	except Exception as exc:
		frappe.db.rollback()
		reason = exc.reason if isinstance(exc, SourceRefreshBlocked) else type(exc).__name__
		_fail_operation(operation_token, payload, reason)
		try:
			write_event(
				"Index Refresh Failed",
				context=context,
				protected_value={"source": source},
				metadata={
					"operation_token": operation_token,
					"processed_records": int(payload.get("processed_records") or 0),
					"total_records": int(payload.get("total_records") or 0),
					"reason": reason,
					"source_remains_disabled": True,
				},
			)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), "CCD Portal source refresh failure audit")
		frappe.log_error(frappe.get_traceback(), "CCD Portal background source refresh failed")
		raise

	payload.update(
		{
			"status": "Completed",
			"stage": "completed",
			"processed_records": processed,
			"total_records": snapshot["total"],
			"completed_at": str(now_datetime()),
			"result": {
				"total": snapshot["total"],
				"indexed": processed,
				"unmapped": 0,
				"failed": 0,
			},
		}
	)
	_set_operation(operation_token, payload)
	return payload["result"]


def _validate_operation_token(operation_token: str) -> str:
	token = str(operation_token or "").strip()
	if len(token) != 32 or any(character not in "0123456789abcdef" for character in token):
		frappe.throw(_("Invalid source refresh operation token."), frappe.ValidationError)
	return token


@frappe.whitelist(methods=["POST"])
def start_source_refresh(source: str, reason: str) -> dict:
	require_post()
	try:
		require_access_administrator()
		context = require_system_manager(reason)
	except Exception:
		audit_denial("Index Refresh Queued")
		raise
	reason = clean_reason(reason)
	existing = _active_operation(source)
	if existing and existing.get("status") in ACTIVE_STATUSES:
		if existing.get("requested_by") != context.user:
			frappe.throw(_("Another System Manager is refreshing this source."), frappe.ValidationError)
		return {
			"operation_token": existing["operation_token"],
			"status": existing["status"],
			"processed_records": int(existing.get("processed_records") or 0),
			"total_records": int(existing.get("total_records") or 0),
			"already_running": True,
		}
	profile = frappe.db.get_value(
		"CCD Portal Source Profile",
		{"source_registration": source, "active": 1},
		["name", "assignment_mode", "sync_pending"],
		as_dict=True,
	)
	if not profile:
		frappe.throw(_("Select an active source assignment."), frappe.ValidationError)
	if profile.assignment_mode == "Per-record Centre Key" and profile.sync_pending:
		frappe.throw(
			_("Complete the source synchronization checks before starting this refresh."),
			frappe.ValidationError,
		)
	return queue_source_refresh(
		profile_name=profile.name,
		source=source,
		reason=reason,
		context=context,
	)


@frappe.whitelist()
def get_source_refresh(operation_token: str) -> dict:
	try:
		require_access_administrator()
		context = require_system_manager("check source refresh")
	except Exception:
		audit_denial("Index Refresh Status")
		raise
	token = _validate_operation_token(operation_token)
	payload = _operation(token)
	if not payload:
		return {"operation_token": token, "status": "Expired"}
	if payload.get("requested_by") != context.user:
		frappe.throw(_("This source refresh belongs to another user."), frappe.PermissionError)
	if payload.get("status") in ACTIVE_STATUSES:
		from frappe.utils.background_jobs import get_job_status

		job_status = get_job_status(f"ccd-portal-source-refresh-{token}")
		if job_status == "finished" and not frappe.db.get_value(
			"CCD Portal Source Profile", payload.get("profile_name"), "sync_pending"
		):
			processed = int(payload.get("processed_records") or 0)
			total = int(payload.get("total_records") or 0)
			payload.update(
				{
					"status": "Completed",
					"stage": "completed",
					"result": {"total": total, "indexed": processed, "unmapped": 0, "failed": 0},
				}
			)
			_set_operation(token, payload)
		elif job_status in FAILED_JOB_STATUSES or job_status == "finished":
			payload.update(
				{
					"status": "Unknown",
					"stage": "unknown",
					"error": _(
						"The background refresh ended without a confirmed result. The source remains disabled."
					),
				}
			)
			_set_operation(token, payload)
	return payload
