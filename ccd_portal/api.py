from __future__ import annotations

import json
import time

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, nowdate
from frappe.utils.password import decrypt, encrypt

from ccd_portal.audit import audit_denial, write_event
from ccd_portal.policy import get_active_policy, policy_field_map, public_policy, reveal_allowed
from ccd_portal.security import (
	CLIENT_AUTHORITIES,
	REVEAL_AUTHORITIES,
	PortalContext,
	canonical_json,
	clean_context,
	clean_reason,
	ensure_record_access,
	mask_value,
	protected_digest,
	require_context,
	require_post,
	require_system_manager,
	search_token,
	validate_search_value,
)

MAX_RESULTS = 20


def _json(value, expected):
	if isinstance(value, expected):
		return value
	try:
		parsed = frappe.parse_json(value)
	except Exception:
		frappe.throw(_("Invalid request."), frappe.ValidationError)
	if not isinstance(parsed, expected):
		frappe.throw(_("Invalid request."), frappe.ValidationError)
	return parsed


def _check_rate_limit(context: PortalContext, operation: str, configured_limit: int) -> None:
	limit = max(cint(configured_limit), 1)
	key = frappe.cache.make_key(f"ccd-portal:{operation}:{context.user}")
	frappe.cache.set(key, 0, ex=60 * 60, nx=True)
	value = frappe.cache.incrby(key, 1)
	if value > limit:
		audit_denial("Rate Limit Exceeded", context=context, metadata={"operation": operation})
		raise frappe.RateLimitExceededError(_("Too many requests. Try again later."))


def _require(event_type: str, *allowed: str, require_centres: bool = False) -> PortalContext:
	try:
		return require_context(*allowed, require_centres=require_centres)
	except Exception:
		audit_denial(event_type)
		raise


def _reason_exists(reason_code: str) -> bool:
	return bool(
		frappe.db.exists("CCD Portal Reveal Reason", {"reason_code": reason_code, "active": 1})
	)


def _search_criteria(criteria, field_map: dict[str, dict]) -> list[dict]:
	criteria = _json(criteria, list)
	if not 1 <= len(criteria) <= 5:
		frappe.throw(_("Use an approved identifier or name with date of birth."), frappe.ValidationError)
	normalized: list[dict] = []
	for item in criteria:
		if not isinstance(item, dict) or set(item) - {"fieldname", "value"}:
			frappe.throw(_("Invalid search criteria."), frappe.ValidationError)
		fieldname = str(item.get("fieldname") or "")
		field_policy = field_map.get(fieldname)
		if not field_policy or not field_policy.get("searchable"):
			frappe.throw(_("Invalid search criteria."), frappe.ValidationError)
		value = validate_search_value(field_policy["data_kind"], item.get("value"))
		normalized.append(
			{
				"fieldname": fieldname,
				"data_kind": field_policy["data_kind"],
				"strong": bool(field_policy.get("strong_identifier")),
				"normalized": value,
			}
		)
	if len({row["fieldname"] for row in normalized}) != len(normalized):
		frappe.throw(_("Each search field may be supplied once."), frappe.ValidationError)
	approved = any(row["strong"] for row in normalized)
	combined_name_dob = any(row["data_kind"] == "Name" for row in normalized) and any(
		row["data_kind"] == "Date" for row in normalized
	)
	if not approved and not combined_name_dob:
		frappe.throw(_("Use an approved identifier or name with date of birth."), frappe.ValidationError)
	return normalized


def _record_document(access: dict, field_policies: list[dict]):
	meta = frappe.get_meta("CCD Master")
	allowed_fields = [row["fieldname"] for row in field_policies if meta.has_field(row["fieldname"])]
	values = frappe.db.get_value("CCD Master", access["ccd_master_record"], allowed_fields, as_dict=True)
	if values is None:
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	return values


def _masked_record(access: dict, field_policies: list[dict]) -> dict:
	values = _record_document(access, field_policies)
	fields = []
	for row in field_policies:
		fieldname = row["fieldname"]
		if fieldname not in values:
			continue
		fields.append(
			{
				"fieldname": fieldname,
				"label": row["label"],
				"classification": row["classification"],
				"value": mask_value(values.get(fieldname), row["mask_strategy"]),
				"has_value": bool(values.get(fieldname)),
				"correctable": bool(row["correctable"]),
			}
		)
	return {
		"id": access["portal_record_id"],
		"centres": sorted((access.get("centres") or "").split(",")),
		"fields": fields,
	}


def _search(context: PortalContext, criteria, *, override_reason: str | None = None) -> dict:
	started = time.perf_counter()
	settings = frappe.get_single("CCD Portal Settings")
	_check_rate_limit(context, "search", settings.searches_per_hour)
	policy, field_map = policy_field_map()
	normalized = _search_criteria(criteria, field_map)
	tokens = [(row["fieldname"], search_token(row["fieldname"], row["normalized"])) for row in normalized]
	conditions = " OR ".join(["(t.fieldname = %s AND t.token = %s)"] * len(tokens))
	values: list = [item for pair in tokens for item in pair]
	scope_clause = ""
	if not context.is_break_glass:
		scope_clause = f" AND rc.centre IN ({', '.join(['%s'] * len(context.centres))})"
	values.extend([policy["policy_version"], nowdate(), nowdate()])
	if not context.is_break_glass:
		values.extend(context.centres)
	values.extend([len(tokens), MAX_RESULTS + 1])
	rows = frappe.db.sql(
		f"""
		SELECT r.portal_record_id
		  FROM `tabCCD Portal Search Token` t
		  JOIN `tabCCD Portal Record` r ON r.name = t.portal_record AND r.active = 1
		  JOIN `tabCCD Portal Record Centre` rc ON rc.portal_record = r.name AND rc.active = 1
		 WHERE t.active = 1 AND ({conditions}) AND t.policy_version = %s
		   AND (rc.effective_from IS NULL OR rc.effective_from <= %s)
		   AND (rc.effective_to IS NULL OR rc.effective_to >= %s)
		   {scope_clause}
		 GROUP BY r.portal_record_id
		HAVING COUNT(DISTINCT t.token) = %s
		 ORDER BY r.portal_record_id
		 LIMIT %s
		""",
		values,
		as_dict=True,
	)
	_, field_policies = get_active_policy()
	results = []
	for row in rows:
		try:
			access = ensure_record_access(row.portal_record_id, context)
		except frappe.PermissionError:
			continue
		results.append(_masked_record(access, field_policies))
		if len(results) == MAX_RESULTS:
			break
	write_event(
		"Search",
		context=context,
		criteria_types=[row["fieldname"] for row in normalized],
		protected_value={
			"criteria": {row["fieldname"]: row["normalized"] for row in normalized},
			"override_reason": override_reason,
		}
		if override_reason
		else {row["fieldname"]: row["normalized"] for row in normalized},
		reason_code="BREAK_GLASS" if override_reason else None,
		metadata={
			"result_count": len(results),
			"truncated": len(rows) > MAX_RESULTS,
			"duration_ms": round((time.perf_counter() - started) * 1000, 2),
		},
	)
	return {"results": results, "maximum_results": MAX_RESULTS, "truncated": len(rows) > MAX_RESULTS}


@frappe.whitelist()
def bootstrap():
	try:
		context = require_context(*CLIENT_AUTHORITIES, "Access Administrator", allow_access_admin=True)
	except Exception:
		audit_denial("Bootstrap")
		raise
	settings = frappe.get_single("CCD Portal Settings")
	response = {
		"user": {"display_name": frappe.get_cached_value("User", context.user, "full_name"), "authority": context.authority},
		"centres": list(context.centres),
		"feature_enabled": bool(settings.enabled),
		"preview_mode": not bool(settings.enabled),
		"reveal_ttl_seconds": cint(settings.reveal_ttl_seconds) or 120,
		"is_access_administrator": context.authority == "Access Administrator",
		"can_activate_policy": context.authority == "Access Administrator"
		and "System Manager" in frappe.get_roles(context.user),
	}
	if context.authority in CLIENT_AUTHORITIES:
		policy, fields = get_active_policy()
		response["policy"] = public_policy(policy, fields, context.authority)
		response["reveal_reasons"] = frappe.get_all(
			"CCD Portal Reveal Reason",
			filters={"active": 1},
			fields=["reason_code", "label"],
			order_by="display_order asc, label asc",
		)
	return response


@frappe.whitelist()
def search(criteria=None):
	require_post()
	context = _require("Search", *CLIENT_AUTHORITIES, require_centres=True)
	try:
		return _search(context, criteria)
	except frappe.RateLimitExceededError:
		raise
	except (frappe.PermissionError, frappe.ValidationError):
		audit_denial("Search", context=context, metadata={"stage": "criteria"})
		raise


@frappe.whitelist()
def detail(record_id: str):
	require_post()
	started = time.perf_counter()
	context = _require("Detail Open", *CLIENT_AUTHORITIES, require_centres=True)
	try:
		access = ensure_record_access(record_id, context)
	except frappe.PermissionError:
		audit_denial("Detail Open", context=context, record_id=record_id)
		raise
	_, fields = get_active_policy()
	result = _masked_record(access, fields)
	write_event(
		"Detail Open",
		context=context,
		portal_record_id=record_id,
		metadata={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
	)
	return result


@frappe.whitelist()
def reveal(record_id: str, reason_code: str, context_note: str | None = None):
	require_post()
	context = _require("PII Reveal", *REVEAL_AUTHORITIES, require_centres=True)
	settings = frappe.get_single("CCD Portal Settings")
	_check_rate_limit(context, "reveal", settings.reveals_per_hour)
	if not _reason_exists(reason_code):
		audit_denial("PII Reveal", context=context, record_id=record_id, metadata={"stage": "reason"})
		frappe.throw(_("Select an approved reveal reason."), frappe.ValidationError)
	try:
		access = ensure_record_access(record_id, context)
	except frappe.PermissionError:
		audit_denial("PII Reveal", context=context, record_id=record_id)
		raise
	_, fields = get_active_policy()
	values = _record_document(access, fields)
	revealed = [
		{"fieldname": row["fieldname"], "label": row["label"], "value": values.get(row["fieldname"])}
		for row in fields
		if reveal_allowed(row, context.authority) and values.get(row["fieldname"]) is not None
	]
	write_event(
		"PII Reveal",
		context=context,
		portal_record_id=record_id,
		reason_code=reason_code,
		protected_value=clean_context(context_note),
		metadata={"revealed_fields": [row["fieldname"] for row in revealed]},
	)
	return {"id": record_id, "fields": revealed, "expires_in": cint(settings.reveal_ttl_seconds) or 120}


@frappe.whitelist()
def submit_correction(record_id: str, changes=None, reason: str | None = None, centre: str | None = None):
	require_post()
	context = _require("Correction Submitted", "Operator", "Data Steward", require_centres=True)
	reason = clean_reason(reason or "")
	changes = _json(changes, dict)
	if not changes or len(changes) > 10:
		frappe.throw(_("Provide one or more permitted changes."), frappe.ValidationError)
	try:
		access = ensure_record_access(record_id, context)
	except frappe.PermissionError:
		audit_denial("Correction Submitted", context=context, record_id=record_id)
		raise
	accessible_centres = sorted(filter(None, (access.get("centres") or "").split(",")))
	centre = str(centre or "").strip()
	if len(accessible_centres) == 1 and not centre:
		centre = accessible_centres[0]
	if centre not in accessible_centres:
		frappe.throw(_("Select one accessible centre for this correction."), frappe.ValidationError)
	policy, field_map = policy_field_map()
	meta = frappe.get_meta("CCD Master")
	clean_changes = {}
	for fieldname, value in changes.items():
		field_policy = field_map.get(fieldname)
		if not field_policy or not field_policy.get("correctable") or not meta.has_field(fieldname):
			frappe.throw(_("A proposed field is not correctable."), frappe.ValidationError)
		value = str(value or "").strip()
		if len(value) > 1000:
			frappe.throw(_("A proposed value is too long."), frappe.ValidationError)
		clean_changes[fieldname] = value
	current = frappe.db.get_value("CCD Master", access["ccd_master_record"], list(clean_changes), as_dict=True)
	source_modified = frappe.db.get_value("CCD Master", access["ccd_master_record"], "modified")
	if current is None or not source_modified:
		audit_denial("Correction Submitted", context=context, record_id=record_id)
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	doc = frappe.get_doc(
		{
			"doctype": "CCD Portal Correction Request",
			"portal_record": access["name"],
			"portal_record_id": record_id,
			"requester": context.user,
			"requester_authority": context.authority,
			"centre": centre,
			"status": "Proposed",
			"policy_version": policy["policy_version"],
			"reason": reason,
			"changed_fields": ",".join(sorted(clean_changes)),
			"encrypted_changes": encrypt(canonical_json(clean_changes)),
			"changes_digest": protected_digest(canonical_json(clean_changes), "correction"),
			"source_snapshot_digest": protected_digest(canonical_json(current or {}), "correction-source"),
			"submitted_source_modified": source_modified,
		}
	)
	doc.insert(ignore_permissions=True)
	write_event(
		"Correction Submitted",
		context=context,
		portal_record_id=record_id,
		protected_value=clean_changes,
		metadata={"request": doc.name, "fields": sorted(clean_changes)},
	)
	return {"request_id": doc.name, "status": doc.status}


@frappe.whitelist()
def corrections():
	context = _require("Correction Queue Opened", "Operator", "Data Steward", require_centres=True)
	if context.authority == "Operator":
		placeholders = ", ".join(["%s"] * len(context.centres))
		rows = frappe.db.sql(
			f"""SELECT name, portal_record_id, centre, status, changed_fields, reason,
			           creation, decision_reason
			      FROM `tabCCD Portal Correction Request`
			     WHERE requester = %s AND centre IN ({placeholders})
			     ORDER BY creation DESC LIMIT 100""",
			[context.user, *context.centres],
			as_dict=True,
		)
	else:
		placeholders = ", ".join(["%s"] * len(context.centres))
		rows = frappe.db.sql(
			f"""SELECT name, portal_record_id, centre, status, changed_fields, reason, creation,
			           decision_reason, requester
			      FROM `tabCCD Portal Correction Request`
			     WHERE centre IN ({placeholders})
			     ORDER BY creation DESC LIMIT 100""",
			list(context.centres),
			as_dict=True,
		)
	write_event("Correction Queue Opened", context=context, metadata={"request_count": len(rows)})
	return {"requests": [dict(row) for row in rows]}


@frappe.whitelist()
def correction_detail(request_id: str, reason_code: str, context_note: str | None = None):
	require_post()
	context = _require("Correction Proposal Reveal", "Data Steward", require_centres=True)
	settings = frappe.get_single("CCD Portal Settings")
	_check_rate_limit(context, "reveal", settings.reveals_per_hour)
	if not _reason_exists(reason_code):
		audit_denial("Correction Proposal Reveal", context=context, metadata={"stage": "reason"})
		frappe.throw(_("Select an approved reveal reason."), frappe.ValidationError)
	request = frappe.db.get_value(
		"CCD Portal Correction Request",
		request_id,
		["name", "portal_record_id", "requester", "centre", "status", "reason", "changed_fields", "encrypted_changes"],
		as_dict=True,
	)
	if not request or request.centre not in context.centres:
		audit_denial("Correction Proposal Reveal", context=context)
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	access = ensure_record_access(request.portal_record_id, context)
	changes = json.loads(decrypt(request.encrypted_changes))
	_, field_map = policy_field_map()
	current = frappe.db.get_value(
		"CCD Master",
		access["ccd_master_record"],
		list(changes),
		as_dict=True,
	)
	comparison = [
		{
			"fieldname": fieldname,
			"label": field_map.get(fieldname, {}).get("label", fieldname),
			"current_value": current.get(fieldname) if current else None,
			"proposed_value": proposed,
		}
		for fieldname, proposed in changes.items()
	]
	write_event(
		"Correction Proposal Reveal",
		context=context,
		portal_record_id=request.portal_record_id,
		reason_code=reason_code,
		protected_value=clean_context(context_note),
		metadata={"request": request.name, "fields": sorted(changes)},
	)
	return {
		"request_id": request.name,
		"requester": request.requester,
		"status": request.status,
		"reason": request.reason,
		"comparison": comparison,
		"expires_in": cint(settings.reveal_ttl_seconds) or 120,
	}


@frappe.whitelist()
def decide_correction(request_id: str, decision: str, reason: str):
	require_post()
	context = _require("Correction Decision", "Data Steward", require_centres=True)
	reason = clean_reason(reason)
	if decision not in {"Approve", "Reject"}:
		frappe.throw(_("Invalid decision."), frappe.ValidationError)
	if not frappe.db.exists("CCD Portal Correction Request", request_id):
		audit_denial("Correction Decision", context=context)
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	request = frappe.get_doc("CCD Portal Correction Request", request_id)
	if request.status != "Proposed" or request.centre not in context.centres:
		audit_denial("Correction Decision", context=context, record_id=request.portal_record_id)
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	if request.requester == context.user:
		audit_denial("Correction Decision", context=context, record_id=request.portal_record_id)
		frappe.throw(_("A steward cannot decide their own request."), frappe.PermissionError)
	access = ensure_record_access(request.portal_record_id, context)
	source_modified = frappe.db.get_value("CCD Master", access["ccd_master_record"], "modified")
	if not source_modified:
		audit_denial("Correction Decision", context=context, record_id=request.portal_record_id)
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	if decision == "Approve" and str(source_modified) != str(request.submitted_source_modified):
		status = "Stale"
	else:
		status = "Approved" if decision == "Approve" else "Rejected"
	frappe.db.set_value(
		"CCD Portal Correction Request",
		request.name,
		{
			"status": status,
			"reviewed_by": context.user,
			"reviewed_on": now_datetime(),
			"decision_reason": reason,
			"approved_source_modified": source_modified if status == "Approved" else None,
		},
		update_modified=False,
	)
	write_event(
		"Correction Decision",
		context=context,
		portal_record_id=request.portal_record_id,
		protected_value=reason,
		metadata={"request": request.name, "decision": status},
	)
	return {"request_id": request.name, "status": status}


@frappe.whitelist()
def break_glass(action: str, reason: str, payload=None):
	"""System Manager-only portal override. Every invocation is a distinct event."""
	require_post()
	try:
		context = require_system_manager(reason)
	except Exception:
		audit_denial("Break Glass Override", metadata={"stage": "authority_or_reason"})
		raise
	reason = clean_reason(reason)
	payload = _json(payload or {}, dict)
	if action == "detail":
		access = ensure_record_access(str(payload.get("record_id") or ""), context)
		_, fields = get_active_policy()
		result = _masked_record(access, fields)
	elif action == "reveal":
		settings = frappe.get_single("CCD Portal Settings")
		_check_rate_limit(context, "reveal", settings.reveals_per_hour)
		access = ensure_record_access(str(payload.get("record_id") or ""), context)
		_, fields = get_active_policy()
		values = _record_document(access, fields)
		result = {
			"id": access["portal_record_id"],
			"fields": [
				{"fieldname": row["fieldname"], "label": row["label"], "value": values.get(row["fieldname"])}
				for row in fields
			],
			"expires_in": cint(settings.reveal_ttl_seconds) or 120,
		}
	elif action == "search":
		result = _search(context, payload.get("criteria"), override_reason=reason)
	elif action == "correction_decision":
		request = frappe.get_doc("CCD Portal Correction Request", str(payload.get("request_id") or ""))
		decision = payload.get("decision")
		if request.status != "Proposed" or decision not in {"Approve", "Reject"}:
			frappe.throw(_("Invalid override decision."), frappe.ValidationError)
		status = "Approved" if decision == "Approve" else "Rejected"
		access = ensure_record_access(request.portal_record_id, context)
		source_modified = frappe.db.get_value("CCD Master", access["ccd_master_record"], "modified")
		if not source_modified:
			frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
		if status == "Approved" and str(source_modified) != str(request.submitted_source_modified):
			status = "Stale"
		frappe.db.set_value(
			"CCD Portal Correction Request",
			request.name,
			{
				"status": status,
				"reviewed_by": context.user,
				"reviewed_on": now_datetime(),
				"decision_reason": reason,
				"approved_source_modified": source_modified if status == "Approved" else None,
			},
			update_modified=False,
		)
		result = {"request_id": request.name, "status": status}
	else:
		frappe.throw(_("Unsupported override action."), frappe.ValidationError)
	write_event(
		"Break Glass Override",
		context=context,
		portal_record_id=payload.get("record_id"),
		reason_code="BREAK_GLASS",
		protected_value=reason,
		metadata={"action": action},
	)
	return result
