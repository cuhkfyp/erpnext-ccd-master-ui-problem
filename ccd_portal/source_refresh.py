from __future__ import annotations

import hashlib
import json
import uuid

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

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


OPERATION_DOCTYPE = "CCD Portal Source Refresh"
OPERATION_TTL_SECONDS = 7 * 24 * 60 * 60
SEGMENT_TIMEOUT_SECONDS = 30 * 60
SEGMENT_SIZE = 1000
MAX_SEGMENT_ATTEMPTS = 3
RECOVERY_GRACE_SECONDS = 2 * 60
ACTIVE_STATUSES = {"Queued", "Running"}
LIVE_JOB_STATUSES = {"queued", "started", "deferred", "scheduled"}
FAILED_JOB_STATUSES = {"failed", "canceled", "stopped"}

OPERATION_FIELDS = (
	"operation_token",
	"source_profile",
	"source_registration",
	"requested_by",
	"status",
	"stage",
	"segment_state",
	"processed_records",
	"total_records",
	"last_record_name",
	"segment_number",
	"attempt",
	"current_job_id",
	"profile_modified",
	"source_latest_modified",
	"governance_snapshot_json",
	"policy_version",
	"completion_event",
	"queued_at",
	"started_at",
	"last_heartbeat",
	"completed_at",
	"blocked_reason",
	"error",
	"result_json",
)


class SourceRefreshBlocked(RuntimeError):
	def __init__(self, reason: str):
		self.reason = str(reason or "index_validation")[:80]
		super().__init__(self.reason)


def _json(value) -> str:
	return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=str)


def _operation_key(operation_token: str) -> str:
	# Retained only to read operations queued by the pre-checkpoint implementation.
	return f"ccd_portal_source_refresh:{operation_token}"


def _active_key(source: str) -> str:
	digest = hashlib.sha256(canonical_source_id(source).encode("utf-8")).hexdigest()[:32]
	return f"ccd_portal_source_refresh_active:{digest}"


def _legacy_operation(operation_token: str) -> dict | None:
	raw = frappe.cache.get_value(_operation_key(operation_token), expires=True)
	if isinstance(raw, bytes):
		raw = raw.decode("utf-8", "replace")
	try:
		payload = json.loads(raw) if raw else None
	except (TypeError, ValueError):
		return None
	return payload if isinstance(payload, dict) else None


def _operation(operation_token: str) -> dict | None:
	if frappe.db.exists("DocType", OPERATION_DOCTYPE):
		row = frappe.db.get_value(
			OPERATION_DOCTYPE,
			operation_token,
			list(OPERATION_FIELDS),
			as_dict=True,
		)
		if row:
			payload = dict(row)
			payload["profile_name"] = payload.get("source_profile")
			payload["source"] = payload.get("source_registration")
			try:
				payload["result"] = json.loads(payload.get("result_json") or "null")
			except (TypeError, ValueError):
				payload["result"] = None
			return payload
	return _legacy_operation(operation_token)


def _set_operation(operation_token: str, payload: dict) -> None:
	if not frappe.db.exists(OPERATION_DOCTYPE, operation_token):
		# A worker from the previous release may still be finishing while code is
		# deployed. Preserve its status without pretending it is resumable.
		frappe.cache.set_value(
			_operation_key(operation_token),
			_json(payload),
			expires_in_sec=OPERATION_TTL_SECONDS,
		)
		return
	values = {field: payload[field] for field in OPERATION_FIELDS if field in payload}
	if "profile_name" in payload:
		values["source_profile"] = payload["profile_name"]
	if "source" in payload:
		values["source_registration"] = payload["source"]
	if "result" in payload:
		values["result_json"] = _json(payload["result"]) if payload["result"] is not None else None
	frappe.db.set_value(OPERATION_DOCTYPE, operation_token, values, update_modified=False)


def _operation_from_names(names: list[str]) -> dict | None:
	return _operation(names[0]) if names else None


def _active_operation(source: str) -> dict | None:
	source_id = canonical_source_id(source)
	if frappe.db.exists("DocType", OPERATION_DOCTYPE):
		names = frappe.get_all(
			OPERATION_DOCTYPE,
			filters={"source_registration": source_id, "status": ["in", sorted(ACTIVE_STATUSES)]},
			pluck="name",
			order_by="creation desc",
			limit=1,
		)
		if names:
			return _operation_from_names(names)
	token = frappe.cache.get_value(_active_key(source_id))
	if isinstance(token, bytes):
		token = token.decode("utf-8", "replace")
	payload = _legacy_operation(str(token)) if token else None
	return payload if payload and payload.get("status") in ACTIVE_STATUSES else None


def _latest_operation(source: str) -> dict | None:
	source_id = canonical_source_id(source)
	if frappe.db.exists("DocType", OPERATION_DOCTYPE):
		names = frappe.get_all(
			OPERATION_DOCTYPE,
			filters={"source_registration": source_id},
			pluck="name",
			order_by="creation desc",
			limit=1,
		)
		if names:
			return _operation_from_names(names)
	token = frappe.cache.get_value(_active_key(source_id))
	if isinstance(token, bytes):
		token = token.decode("utf-8", "replace")
	return _legacy_operation(str(token)) if token else None


def assert_source_refresh_available(source: str) -> None:
	if _active_operation(source):
		frappe.throw(
			_("A background refresh is already running for this source. Wait for it to finish before changing the configuration."),
			frappe.ValidationError,
		)


def operation_for_source(source: str, viewer: str) -> dict:
	payload = _latest_operation(source)
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


def _job_id(operation_token: str, segment_number: int, attempt: int) -> str:
	return f"ccd-portal-source-refresh-{operation_token}-{int(segment_number)}-{int(attempt)}"


def _enqueue_segment(payload: dict, *, enqueue_after_commit: bool = False) -> None:
	frappe.enqueue(
		"ccd_portal.source_refresh.run_source_refresh_segment",
		queue="long",
		timeout=SEGMENT_TIMEOUT_SECONDS,
		enqueue_after_commit=enqueue_after_commit,
		job_id=payload["current_job_id"],
		operation_token=payload["operation_token"],
		expected_segment=int(payload.get("segment_number") or 0),
		expected_attempt=int(payload.get("attempt") or 0),
		expected_cursor=str(payload.get("last_record_name") or ""),
	)


def queue_source_refresh(
	*,
	profile_name: str,
	source: str,
	reason: str,
	context: PortalContext,
	completion_event: str = "Index Refresh",
) -> dict:
	"""Commit a fail-closed source gate and enqueue the first durable segment."""
	reason = clean_reason(reason)
	source_id = canonical_source_id(source)
	locked = frappe.db.sql(
		"SELECT name FROM `tabCCD Portal Source Profile` WHERE name=%s FOR UPDATE",
		(profile_name,),
	)
	if not locked:
		frappe.throw(_("Select an active source assignment."), frappe.ValidationError)
	if _active_operation(source_id):
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
	profile_modified = str(frappe.db.get_value("CCD Portal Source Profile", profile.name, "modified"))
	source_snapshot = _source_snapshot(source_id)
	governance_snapshot = _governance_snapshot(profile.name)
	policy, _ = get_active_policy()
	operation_token = uuid.uuid4().hex
	queued_at = now_datetime()
	payload = {
		"operation_token": operation_token,
		"source_profile": profile.name,
		"source_registration": source_id,
		"requested_by": context.user,
		"status": "Queued",
		"stage": "queued",
		"segment_state": "Queued",
		"processed_records": 0,
		"total_records": source_snapshot["total"],
		"last_record_name": "",
		"segment_number": 0,
		"attempt": 0,
		"current_job_id": _job_id(operation_token, 0, 0),
		"profile_modified": profile_modified,
		"source_latest_modified": source_snapshot["latest_modified"],
		"governance_snapshot_json": _json(governance_snapshot),
		"policy_version": policy["policy_version"],
		"completion_event": completion_event,
		"queued_at": queued_at,
		"last_heartbeat": queued_at,
	}
	frappe.get_doc({"doctype": OPERATION_DOCTYPE, **payload}).insert(ignore_permissions=True)
	frappe.cache.set_value(_active_key(source_id), operation_token, expires_in_sec=OPERATION_TTL_SECONDS)
	write_event(
		"Index Refresh Queued",
		context=context,
		protected_value={"source": source_id, "reason": reason},
		metadata={
			"operation_token": operation_token,
			"total_records": source_snapshot["total"],
			"background": True,
			"checkpointed": True,
			"segment_size": SEGMENT_SIZE,
		},
	)
	frappe.db.commit()
	try:
		_enqueue_segment(payload)
	except Exception:
		# The durable queued operation is recoverable by polling or the scheduler.
		frappe.log_error(frappe.get_traceback(), "CCD Portal source refresh initial enqueue delayed")
	return {"operation_token": operation_token, "status": "Queued", "total_records": source_snapshot["total"]}


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


def _lock_operation(operation_token: str) -> dict | None:
	locked = frappe.db.sql(
		"SELECT name FROM `tabCCD Portal Source Refresh` WHERE name=%s FOR UPDATE",
		(operation_token,),
	)
	return _operation(operation_token) if locked else None


def _validate_operation(payload: dict) -> dict:
	profile_name = payload["source_profile"]
	source = canonical_source_id(payload["source_registration"])
	profile = frappe.db.get_value(
		"CCD Portal Source Profile",
		profile_name,
		["name", "canonical_source_id", "sync_pending", "active", "modified"],
		as_dict=True,
	)
	if (
		not profile
		or not profile.active
		or canonical_source_id(profile.canonical_source_id) != source
		or not profile.sync_pending
		or str(profile.modified) != str(payload.get("profile_modified"))
	):
		raise SourceRefreshBlocked("source_profile_changed")
	source_snapshot = _source_snapshot(source)
	if (
		source_snapshot["total"] != int(payload.get("total_records") or 0)
		or source_snapshot["latest_modified"] != str(payload.get("source_latest_modified") or "")
	):
		raise SourceRefreshBlocked("source_changed_during_refresh")
	if _json(_governance_snapshot(profile_name)) != str(payload.get("governance_snapshot_json") or ""):
		raise SourceRefreshBlocked("centre_governance_changed_during_refresh")
	current_policy, _ = get_active_policy()
	if current_policy["policy_version"] != payload.get("policy_version"):
		raise SourceRefreshBlocked("policy_changed_during_refresh")
	index_context = source_index_context(source, allow_sync_pending=True)
	if not index_context.get("profile"):
		raise SourceRefreshBlocked("source_profile_missing")
	return index_context


def _claim_segment(
	operation_token: str,
	expected_segment: int,
	expected_attempt: int,
	expected_cursor: str,
) -> dict | None:
	payload = _lock_operation(operation_token)
	if not payload or payload.get("status") not in ACTIVE_STATUSES:
		frappe.db.rollback()
		return None
	if (
		int(payload.get("segment_number") or 0) != int(expected_segment)
		or int(payload.get("attempt") or 0) != int(expected_attempt)
		or str(payload.get("last_record_name") or "") != str(expected_cursor or "")
		or payload.get("segment_state") != "Queued"
	):
		frappe.db.rollback()
		return None
	now = now_datetime()
	payload.update(
		{
			"status": "Running",
			"segment_state": "Running",
			"started_at": payload.get("started_at") or now,
			"last_heartbeat": now,
		}
	)
	_set_operation(operation_token, payload)
	frappe.db.commit()
	return _operation(operation_token)


def _fail_operation(operation_token: str, payload: dict, reason: str) -> None:
	payload.update(
		{
			"status": "Failed",
			"stage": "failed",
			"segment_state": "Complete",
			"completed_at": now_datetime(),
			"last_heartbeat": now_datetime(),
			"error": _(
				"The background index refresh failed. The source remains disabled; review the server error log before retrying."
			),
			"blocked_reason": str(reason or "index_failure")[:80],
		}
	)
	context = PortalContext(
		user=payload.get("requested_by") or "Administrator",
		authority="System Manager",
		centres=(),
		is_break_glass=True,
	)
	try:
		_set_operation(operation_token, payload)
		write_event(
			"Index Refresh Failed",
			context=context,
			protected_value={"source": payload.get("source_registration")},
			metadata={
				"operation_token": operation_token,
				"processed_records": int(payload.get("processed_records") or 0),
				"total_records": int(payload.get("total_records") or 0),
				"reason": payload["blocked_reason"],
				"source_remains_disabled": True,
				"checkpointed": True,
			},
		)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		try:
			_set_operation(operation_token, payload)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "CCD Portal source refresh failure audit")


def _complete_operation(operation_token: str, payload: dict) -> dict:
	_validate_operation(payload)
	processed = int(payload.get("processed_records") or 0)
	total = int(payload.get("total_records") or 0)
	if processed != total:
		raise SourceRefreshBlocked("checkpoint_count_mismatch")
	payload.update({"stage": "publishing", "last_heartbeat": now_datetime()})
	_set_operation(operation_token, payload)
	_publish_source_index(payload["source_registration"])
	frappe.db.set_value("CCD Portal Source Profile", payload["source_profile"], "sync_pending", 0)
	result = {"total": total, "indexed": processed, "unmapped": 0, "failed": 0}
	payload.update(
		{
			"status": "Completed",
			"stage": "completed",
			"segment_state": "Complete",
			"completed_at": now_datetime(),
			"last_heartbeat": now_datetime(),
			"result": result,
			"error": None,
			"blocked_reason": None,
		}
	)
	_set_operation(operation_token, payload)
	context = PortalContext(
		user=payload["requested_by"],
		authority="System Manager",
		centres=(),
		is_break_glass=True,
	)
	metadata = {
		"operation_token": operation_token,
		**result,
		"background": True,
		"strict_source_gate": True,
		"checkpointed": True,
		"segments": int(payload.get("segment_number") or 0),
	}
	write_event(
		"Index Refresh",
		context=context,
		protected_value={"source": payload["source_registration"]},
		metadata=metadata,
	)
	completion_event = str(payload.get("completion_event") or "Index Refresh")
	if completion_event != "Index Refresh":
		write_event(
			completion_event,
			context=context,
			protected_value={"source": payload["source_registration"]},
			metadata=metadata,
		)
	frappe.db.commit()
	frappe.cache.delete_value(_active_key(payload["source_registration"]))
	return result


def run_source_refresh_segment(
	operation_token: str,
	expected_segment: int,
	expected_attempt: int,
	expected_cursor: str,
) -> dict:
	"""Build one idempotent inactive segment and durably queue its successor."""
	payload = _operation(operation_token)
	if not payload:
		raise frappe.PermissionError("Source refresh operation is unavailable")
	requested_by = payload.get("requested_by")
	if "System Manager" not in set(frappe.get_roles(requested_by)):
		_fail_operation(operation_token, payload, "system_manager_role_removed")
		raise frappe.PermissionError("System Manager role is required")
	frappe.set_user(requested_by)
	payload = _claim_segment(
		operation_token,
		expected_segment,
		expected_attempt,
		expected_cursor,
	)
	if not payload:
		return {"status": "Stale segment ignored"}

	try:
		if payload.get("stage") in {"queued", "staging"}:
			payload.update({"stage": "staging", "last_heartbeat": now_datetime()})
			_set_operation(operation_token, payload)
			frappe.db.commit()
			_stage_source_index(payload["source_registration"])
			_validate_operation(payload)
			payload.update({"stage": "indexing", "last_heartbeat": now_datetime()})
			_set_operation(operation_token, payload)
			frappe.db.commit()

		index_context = _validate_operation(payload)
		names = frappe.db.sql(
			"""
			SELECT name
			  FROM `tabCCD Master`
			 WHERE ccd_reg_source=%s AND name>%s
			 ORDER BY name
			 LIMIT %s
			""",
			(
				payload["source_registration"],
				str(payload.get("last_record_name") or ""),
				SEGMENT_SIZE,
			),
			pluck=True,
		)
		if not names:
			return _complete_operation(operation_token, payload)

		segment_number = int(payload.get("segment_number") or 0)
		for position, name in enumerate(names):
			save_point = f"ccd_portal_source_{segment_number}_{position}"
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

		_validate_operation(payload)
		processed = int(payload.get("processed_records") or 0) + len(names)
		next_segment = segment_number + 1
		payload.update(
			{
				"status": "Running",
				"stage": "indexing",
				"segment_state": "Queued",
				"processed_records": processed,
				"last_record_name": names[-1],
				"segment_number": next_segment,
				"attempt": 0,
				"current_job_id": _job_id(operation_token, next_segment, 0),
				"last_heartbeat": now_datetime(),
			}
		)
		_set_operation(operation_token, payload)
		# The index writes and cursor move become visible atomically. A hard stop
		# before this commit rolls back the whole segment; after it, recovery starts
		# at the new cursor and never duplicates the committed rows.
		frappe.db.commit()
	except Exception as exc:
		frappe.db.rollback()
		reason = exc.reason if isinstance(exc, SourceRefreshBlocked) else type(exc).__name__
		_fail_operation(operation_token, payload, reason)
		frappe.log_error(frappe.get_traceback(), "CCD Portal background source refresh failed")
		raise

	try:
		_enqueue_segment(payload)
	except Exception:
		# The cursor is already durable. Polling or the scheduler will enqueue a
		# fresh attempt without replaying the committed segment.
		frappe.log_error(frappe.get_traceback(), "CCD Portal source refresh continuation delayed")
	return {
		"status": "Running",
		"processed_records": int(payload.get("processed_records") or 0),
		"total_records": int(payload.get("total_records") or 0),
	}


def _job_status(job_id: str) -> str:
	if not job_id:
		return ""
	from frappe.utils.background_jobs import get_job_status

	status = get_job_status(job_id)
	return str(getattr(status, "value", status) or "").lower()


def _seconds_since(value) -> float:
	if not value:
		return float(RECOVERY_GRACE_SECONDS + 1)
	return max(0.0, (now_datetime() - get_datetime(value)).total_seconds())


def _recover_operation(operation_token: str) -> dict | None:
	payload = _operation(operation_token)
	if not payload or payload.get("status") not in ACTIVE_STATUSES:
		return payload
	job_status = _job_status(str(payload.get("current_job_id") or ""))
	if job_status in LIVE_JOB_STATUSES:
		return payload
	if job_status not in FAILED_JOB_STATUSES and _seconds_since(payload.get("last_heartbeat")) < RECOVERY_GRACE_SECONDS:
		return payload

	locked = _lock_operation(operation_token)
	if not locked or locked.get("status") not in ACTIVE_STATUSES:
		frappe.db.rollback()
		return locked
	# Recheck under the database lock in case another poller or the scheduler
	# recovered this exact segment while we were waiting.
	current_status = _job_status(str(locked.get("current_job_id") or ""))
	if current_status in LIVE_JOB_STATUSES:
		frappe.db.rollback()
		return locked
	if current_status not in FAILED_JOB_STATUSES and _seconds_since(locked.get("last_heartbeat")) < RECOVERY_GRACE_SECONDS:
		frappe.db.rollback()
		return locked

	next_attempt = int(locked.get("attempt") or 0) + 1
	if next_attempt >= MAX_SEGMENT_ATTEMPTS:
		frappe.db.rollback()
		_fail_operation(operation_token, locked, "segment_retry_exhausted")
		return _operation(operation_token)
	locked.update(
		{
			"status": "Running",
			"segment_state": "Queued",
			"attempt": next_attempt,
			"current_job_id": _job_id(
				operation_token,
				int(locked.get("segment_number") or 0),
				next_attempt,
			),
			"last_heartbeat": now_datetime(),
		}
	)
	_set_operation(operation_token, locked)
	frappe.db.commit()
	try:
		_enqueue_segment(locked)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CCD Portal source refresh recovery enqueue delayed")
	return _operation(operation_token)


def recover_source_refreshes() -> None:
	"""Scheduler safety net for a worker/container interruption between segments."""
	if not frappe.db.exists("DocType", OPERATION_DOCTYPE):
		return
	names = frappe.get_all(
		OPERATION_DOCTYPE,
		filters={"status": ["in", sorted(ACTIVE_STATUSES)]},
		pluck="name",
		order_by="creation asc",
		limit=100,
	)
	for operation_token in names:
		try:
			_recover_operation(operation_token)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), "CCD Portal source refresh recovery failed")


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
	if existing:
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


def _public_operation(payload: dict) -> dict:
	result = {
		"operation_token": payload.get("operation_token"),
		"status": payload.get("status"),
		"stage": payload.get("stage"),
		"processed_records": int(payload.get("processed_records") or 0),
		"total_records": int(payload.get("total_records") or 0),
		"last_heartbeat": payload.get("last_heartbeat"),
	}
	for field in ("completed_at", "error", "blocked_reason", "result"):
		if payload.get(field) not in (None, ""):
			result[field] = payload[field]
	return result


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
	if payload.get("status") in ACTIVE_STATUSES and frappe.db.exists(OPERATION_DOCTYPE, token):
		payload = _recover_operation(token) or payload
	return _public_operation(payload)
