from __future__ import annotations

import json
import time

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, nowdate
from frappe.utils.password import decrypt, encrypt

from ccd_portal.audit import audit_denial, write_event
from ccd_portal.identity import (
	feature_state,
	opaque_person_id,
	person_for_number,
	person_for_record,
	persons_for_records,
)
from ccd_portal.identity_rules import bounded_record_order
from ccd_portal.policy import get_active_policy, policy_field_map, public_policy, reveal_allowed
from ccd_portal.primitives import (
	SERVICE_FALLBACK_UNSPECIFIED,
	is_restricted_name_search,
	is_valid_unified_person_number,
	resolve_service_name,
)
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
CORRECTION_REVIEW_REASON = "CORRECTION_REVIEW"


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
		frappe.throw(
			_("Use an approved identifier, an exact First Name, an exact same-language First Name and Surname, or a name with date of birth."),
			frappe.ValidationError,
		)
	normalized: list[dict] = []
	for item in criteria:
		if not isinstance(item, dict) or set(item) - {"fieldname", "value"}:
			frappe.throw(_("Invalid search criteria."), frappe.ValidationError)
		fieldname = str(item.get("fieldname") or "")
		field_policy = field_map.get(fieldname)
		if not field_policy or not field_policy.get("searchable"):
			frappe.throw(_("Invalid search criteria."), frappe.ValidationError)
		source = field_policy.get("source") or "CCD Master"
		if source == "Unified Person":
			value = str(item.get("value") or "").strip()
			if not is_valid_unified_person_number(value):
				frappe.throw(_("Enter a complete, valid Unified Person Number."), frappe.ValidationError)
		else:
			value = validate_search_value(field_policy["data_kind"], item.get("value"))
		normalized.append(
			{
				"fieldname": fieldname,
				"source": source,
				"data_kind": field_policy["data_kind"],
				"strong": bool(field_policy.get("strong_identifier")),
				"normalized": value,
			}
		)
	if len({row["fieldname"] for row in normalized}) != len(normalized):
		frappe.throw(_("Each search field may be supplied once."), frappe.ValidationError)
	unified = [row for row in normalized if row["source"] == "Unified Person"]
	if unified and (len(unified) != 1 or len(normalized) != 1):
		frappe.throw(_("Unified Person Number must be searched on its own."), frappe.ValidationError)
	approved = any(row["strong"] for row in normalized)
	combined_name_dob = any(row["data_kind"] == "Name" for row in normalized) and any(
		row["data_kind"] == "Date" for row in normalized
	)
	if not approved and not combined_name_dob and not is_restricted_name_search(normalized):
		frappe.throw(
			_("Use an approved identifier, an exact First Name, an exact same-language First Name and Surname, or a name with date of birth."),
			frappe.ValidationError,
		)
	return normalized


def _record_document(access: dict, field_policies: list[dict]):
	meta = frappe.get_meta("CCD Master")
	allowed_fields = [
		row["fieldname"]
		for row in field_policies
		if (row.get("source") or "CCD Master") == "CCD Master" and meta.has_field(row["fieldname"])
	]
	values = frappe.db.get_value("CCD Master", access["ccd_master_record"], allowed_fields, as_dict=True)
	if values is None:
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	return values


def _masked_record(
	access: dict,
	field_policies: list[dict],
	service_name_fallback: str = SERVICE_FALLBACK_UNSPECIFIED,
) -> dict:
	values = _record_document(access, field_policies)
	fields = []
	for row in field_policies:
		if (row.get("source") or "CCD Master") != "CCD Master":
			continue
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
		"service_name": resolve_service_name(
			access.get("record_service_name"),
			access.get("registration_service_name"),
			access.get("fallback_service_source"),
			service_name_fallback,
		),
		"centres": sorted((access.get("centres") or "").split(",")),
		"fields": fields,
	}


def _service_names(
	accesses: list[dict], service_name_fallback: str = SERVICE_FALLBACK_UNSPECIFIED
) -> list[str]:
	"""Return distinct display-only service labels for already-authorized records."""
	services = []
	seen = set()
	for access in accesses:
		service = resolve_service_name(
			access.get("record_service_name"),
			access.get("registration_service_name"),
			access.get("fallback_service_source"),
			service_name_fallback,
		)
		if service not in seen:
			seen.add(service)
			services.append(service)
	return services


def _person_fields(person: dict | None, *, revealed: bool = False) -> list[dict]:
	if not person:
		return []
	value = person["unified_person_number"]
	return [
		{
			"fieldname": "unified_person_number",
			"label": "Unified Person Number",
			"classification": "Identity",
			"value": value if revealed else mask_value(value, "Last 4"),
			"has_value": True,
			"correctable": False,
		}
	]


def _identity_review_fields(field_policies: list[dict]) -> list[dict]:
	"""Return current policy-governed CCD identity fields for temporary review."""
	meta = frappe.get_meta("CCD Master")
	return [
		row
		for row in field_policies
		if (row.get("source") or "CCD Master") == "CCD Master"
		and row.get("classification") == "Identity"
		and meta.has_field(row.get("fieldname"))
	]


def _same_person_state(context: PortalContext, policy: dict, settings) -> dict:
	try:
		return feature_state(policy, settings, context)
	except Exception:
		return {"effective": False, "policy_enabled": False, "rollout_enabled": False, "schema_ready": False}


def _require_correction_feature(context: PortalContext, event_type: str, record_id: str | None = None):
	settings = frappe.get_single("CCD Portal Settings")
	if cint(getattr(settings, "correction_requests_enabled", 0)):
		return settings
	audit_denial(
		event_type,
		context=context,
		record_id=record_id,
		metadata={"stage": "feature_disabled"},
	)
	frappe.throw(_("The correction workflow is not currently available."), frappe.PermissionError)


def _require_identity_concern_feature(
	context: PortalContext, event_type: str, record_id: str | None = None
):
	"""Gate staff reporting while allowing qualified reviewers to close existing concerns."""
	settings = frappe.get_single("CCD Portal Settings")
	if cint(getattr(settings, "identity_concerns_enabled", 1)):
		return settings
	audit_denial(
		event_type,
		context=context,
		record_id=record_id,
		metadata={"stage": "feature_disabled"},
	)
	frappe.throw(_("Identity concern reporting is not currently available."), frappe.PermissionError)


def _accessible_records_for_person(
	person: dict, context: PortalContext, policy_version: str
) -> list[dict]:
	record_names = person.get("ccd_master_records") or []
	if not record_names:
		return []
	placeholders = ", ".join(["%s"] * len(record_names))
	rows = frappe.db.sql(
		f"""
		SELECT r.portal_record_id
		  FROM `tabCCD Portal Record` r
		  JOIN `tabCCD Portal Source Profile` sp
		    ON sp.canonical_source_id = r.source_registration
		   AND sp.active = 1 AND COALESCE(sp.sync_pending, 0) = 0
		 WHERE r.active = 1 AND r.policy_version = %s AND r.ccd_master_record IN ({placeholders})
		 ORDER BY r.portal_record_id
		""",
		[policy_version, *record_names],
		as_dict=True,
	)
	accessible = []
	seen_underlying = set()
	for row in rows:
		try:
			access = ensure_record_access(row.portal_record_id, context)
		except frappe.PermissionError:
			continue
		if access["ccd_master_record"] in seen_underlying:
			continue
		seen_underlying.add(access["ccd_master_record"])
		accessible.append(access)
	return accessible


def _common_person_service_names(
	accesses: list[dict],
	context: PortalContext,
	policy_version: str,
	service_name_fallback: str,
	*,
	identity_effective: bool,
	truncated: bool,
) -> list[str]:
	"""Return accessible service labels only when every visible match is one eligible person."""
	if not identity_effective or truncated or not accesses:
		return []
	if any(access.get("policy_version") != policy_version for access in accesses):
		return []
	record_names = sorted({str(access["ccd_master_record"]) for access in accesses})
	if not record_names:
		return []
	try:
		people = persons_for_records(record_names)
		if set(people) != set(record_names):
			return []
		person_names = {str(person["name"]) for person in people.values()}
		if len(person_names) != 1:
			return []
		person = next(iter(people.values()))
		accessible = _accessible_records_for_person(person, context, policy_version)
		return _service_names(accessible, service_name_fallback) if accessible else []
	except Exception:
		# Identity integration remains additive; ambiguous or unavailable identity state shows no summary.
		return []


def _grouped_response(
	direct_accesses: list[dict],
	context: PortalContext,
	field_policies: list[dict],
	*,
	policy_version: str,
	expand: bool,
	group_direct_matches: bool = False,
	service_name_fallback: str = SERVICE_FALLBACK_UNSPECIFIED,
	direct_truncated: bool = False,
) -> dict:
	unique_direct = []
	direct_underlying = set()
	for access in direct_accesses:
		if access["ccd_master_record"] in direct_underlying:
			continue
		direct_underlying.add(access["ccd_master_record"])
		unique_direct.append(access)
	direct_accesses = unique_direct
	direct_order = [row["portal_record_id"] for row in direct_accesses]
	direct_ids = set(direct_order)
	person_by_record: dict[str, dict] = {}
	if expand or group_direct_matches:
		try:
			current_accesses = [
				access for access in direct_accesses if access.get("policy_version") == policy_version
			]
			people = persons_for_records([access["ccd_master_record"] for access in current_accesses])
			for access in current_accesses:
				person = people.get(access["ccd_master_record"])
				if person:
					person_by_record[access["portal_record_id"]] = person
		except Exception:
			# Identity integration is additive. Its outage never removes a direct match.
			person_by_record = {}
			expand = False

	groups: list[dict] = []
	group_map: dict[str, dict] = {}
	seen_records: set[str] = set()
	seen_underlying: set[str] = set()
	for access in direct_accesses:
		record_id = access["portal_record_id"]
		person = person_by_record.get(record_id)
		key = f"person:{person['name']}" if person else f"record:{record_id}"
		group = group_map.get(key)
		if not group:
			group = {
				"person_id": opaque_person_id(person["name"] if person else f"record:{record_id}"),
				"relationship_label": "",
				# Weak-name results may group confirmed direct matches, but do not expose
				# the person number until a normal detail/expansion path is used.
				"person_fields": _person_fields(person) if expand else [],
				"records": [],
				"_person": person,
			}
			group_map[key] = group
			groups.append(group)
		if record_id not in seen_records:
			record = _masked_record(access, field_policies, service_name_fallback)
			record["direct_match"] = True
			group["records"].append(record)
			seen_records.add(record_id)
			seen_underlying.add(access["ccd_master_record"])

	truncated = direct_truncated
	if expand:
		candidates: list[tuple[dict, dict]] = []
		candidate_ids: set[str] = set()
		candidate_underlying: set[str] = set()
		related_groups: list[list[str]] = []
		for group in groups:
			person = group["_person"]
			if not person:
				continue
			try:
				related = _accessible_records_for_person(person, context, policy_version)
			except Exception:
				continue
			group_ids = []
			for access in related:
				record_id = access["portal_record_id"]
				underlying_id = access["ccd_master_record"]
				if (
					record_id in seen_records
					or record_id in candidate_ids
					or underlying_id in seen_underlying
					or underlying_id in candidate_underlying
				):
					continue
				candidate_ids.add(record_id)
				candidate_underlying.add(underlying_id)
				group_ids.append(record_id)
				candidates.append((group, access))
			related_groups.append(group_ids)
		selected_order, capacity_truncated = bounded_record_order(direct_order, related_groups, MAX_RESULTS)
		selected_ids = set(selected_order)
		truncated = truncated or capacity_truncated
		for group, access in candidates:
			record_id = access["portal_record_id"]
			if record_id not in selected_ids:
				continue
			record = _masked_record(access, field_policies, service_name_fallback)
			record["direct_match"] = record_id in direct_ids
			group["records"].append(record)
			seen_records.add(record_id)
			seen_underlying.add(access["ccd_master_record"])

	for group in groups:
		if len(group["records"]) > 1:
			group["relationship_label"] = "Confirmed same person"
		group.pop("_person", None)
	return {
		"results": groups,
		"record_count": len(seen_records),
		"maximum_records": MAX_RESULTS,
		"truncated": bool(truncated),
	}


def _number_lookup_response(
	person: dict,
	accesses: list[dict],
	field_policies: list[dict],
	*,
	truncated: bool,
	service_name_fallback: str = SERVICE_FALLBACK_UNSPECIFIED,
) -> dict:
	records = []
	seen_underlying = set()
	for access in accesses[:MAX_RESULTS]:
		if access["ccd_master_record"] in seen_underlying:
			continue
		seen_underlying.add(access["ccd_master_record"])
		record = _masked_record(access, field_policies, service_name_fallback)
		# The person number matched the canonical person, not a value copied onto
		# any individual CCD record.
		record["direct_match"] = False
		records.append(record)
	group = {
		"person_id": opaque_person_id(person["name"]),
		"relationship_label": "Confirmed same person" if len(records) > 1 else "",
		"person_fields": _person_fields(person),
		"records": records,
	}
	return {
		"results": [group] if records else [],
		"record_count": len(records),
		"maximum_records": MAX_RESULTS,
		"truncated": bool(truncated),
	}


def _search(context: PortalContext, criteria, *, override_reason: str | None = None) -> dict:
	started = time.perf_counter()
	settings = frappe.get_single("CCD Portal Settings")
	_check_rate_limit(context, "search", settings.searches_per_hour)
	policy, field_map = policy_field_map()
	normalized = _search_criteria(criteria, field_map)
	restricted_name_search = is_restricted_name_search(normalized)
	if restricted_name_search:
		_check_rate_limit(
			context,
			"first-name-search",
			getattr(settings, "chinese_first_name_searches_per_hour", 10) or 10,
		)
	_, field_policies = get_active_policy()
	state = _same_person_state(context, policy, settings)
	same_person_applied = bool(state["effective"] and not restricted_name_search)
	direct_accesses: list[dict] = []
	direct_truncated = False
	if normalized[0]["source"] == "Unified Person":
		person = None
		if state["effective"]:
			try:
				person = person_for_number(normalized[0]["normalized"])
				if person:
					visible = _accessible_records_for_person(person, context, policy["policy_version"])
					direct_truncated = len(visible) > MAX_RESULTS
					direct_accesses = visible[:MAX_RESULTS]
			except Exception:
				# A number lookup never falls back to an unsafe partial identity answer.
				direct_accesses = []
		result = (
			_number_lookup_response(
				person,
				direct_accesses,
				field_policies,
				truncated=direct_truncated,
				service_name_fallback=(
					getattr(settings, "service_name_fallback", None) or SERVICE_FALLBACK_UNSPECIFIED
				),
			)
			if person
			else {
				"results": [],
				"record_count": 0,
				"maximum_records": MAX_RESULTS,
				"truncated": False,
			}
		)
	else:
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
			  JOIN `tabCCD Portal Source Profile` sp
			    ON sp.canonical_source_id = r.source_registration
			   AND sp.active = 1 AND COALESCE(sp.sync_pending, 0) = 0
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
		for row in rows:
			try:
				direct_accesses.append(ensure_record_access(row.portal_record_id, context))
			except frappe.PermissionError:
				continue
			if len(direct_accesses) == MAX_RESULTS:
				break
		direct_truncated = len(rows) > MAX_RESULTS
		result = _grouped_response(
			direct_accesses,
			context,
			field_policies,
			policy_version=policy["policy_version"],
			expand=same_person_applied,
			group_direct_matches=bool(state["effective"] and restricted_name_search),
			service_name_fallback=(
				getattr(settings, "service_name_fallback", None) or SERVICE_FALLBACK_UNSPECIFIED
			),
			direct_truncated=direct_truncated,
		)
		if restricted_name_search:
			result["services_used"] = _common_person_service_names(
				direct_accesses,
				context,
				policy["policy_version"],
				getattr(settings, "service_name_fallback", None)
				or SERVICE_FALLBACK_UNSPECIFIED,
				identity_effective=bool(state["effective"]),
				truncated=direct_truncated,
			)
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
			"result_count": result["record_count"],
			"truncated": result["truncated"],
			"same_person_applied": same_person_applied,
			"duration_ms": round((time.perf_counter() - started) * 1000, 2),
		},
	)
	return result


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
		"correction_requests_enabled": bool(
			cint(getattr(settings, "correction_requests_enabled", 0))
		),
		"identity_concerns_enabled": bool(
			cint(getattr(settings, "identity_concerns_enabled", 1))
		),
		"reveal_ttl_seconds": cint(settings.reveal_ttl_seconds) or 120,
		"is_access_administrator": context.authority == "Access Administrator",
		"can_activate_policy": context.authority == "Access Administrator"
		and "System Manager" in frappe.get_roles(context.user),
	}
	if context.authority in CLIENT_AUTHORITIES:
		policy, fields = get_active_policy()
		same_person = _same_person_state(context, policy, settings)
		published_policy = public_policy(policy, fields, context.authority)
		if not same_person["effective"]:
			published_policy["fields"] = [
				row for row in published_policy["fields"] if row.get("source") != "Unified Person"
			]
		response["policy"] = published_policy
		response["same_person"] = same_person
		response["can_report_identity_concern"] = bool(
			cint(getattr(settings, "identity_concerns_enabled", 1))
		) and context.authority in {"Operator", "Data Steward"}
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
	settings = frappe.get_single("CCD Portal Settings")
	policy, fields = get_active_policy()
	service_name_fallback = (
		getattr(settings, "service_name_fallback", None) or SERVICE_FALLBACK_UNSPECIFIED
	)
	result = _masked_record(
		access,
		fields,
		service_name_fallback,
	)
	result["can_expand_same_person"] = False
	result["services_used"] = _service_names([access], service_name_fallback)
	if (
		access.get("policy_version") == policy["policy_version"]
		and _same_person_state(context, policy, settings)["effective"]
	):
		try:
			person = person_for_record(access["ccd_master_record"])
		except Exception:
			person = None
		result["person_id"] = opaque_person_id(person["name"]) if person else ""
		result["person_fields"] = _person_fields(person)
		if person:
			try:
				accessible = _accessible_records_for_person(person, context, policy["policy_version"])
			except Exception:
				accessible = []
			result["can_expand_same_person"] = len(accessible) > 1
			if accessible:
				result["services_used"] = _service_names(accessible, service_name_fallback)
	write_event(
		"Detail Open",
		context=context,
		portal_record_id=record_id,
		metadata={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
	)
	return result


@frappe.whitelist()
def service_summary(record_id: str):
	"""Return only distinct accessible service labels for one selected person."""
	require_post()
	started = time.perf_counter()
	context = _require("Detail Open", *CLIENT_AUTHORITIES, require_centres=True)
	try:
		access = ensure_record_access(record_id, context)
	except frappe.PermissionError:
		audit_denial("Detail Open", context=context, record_id=record_id)
		raise
	settings = frappe.get_single("CCD Portal Settings")
	policy, _fields = get_active_policy()
	service_name_fallback = (
		getattr(settings, "service_name_fallback", None) or SERVICE_FALLBACK_UNSPECIFIED
	)
	accessible = [access]
	if (
		access.get("policy_version") == policy["policy_version"]
		and _same_person_state(context, policy, settings)["effective"]
	):
		try:
			person = person_for_record(access["ccd_master_record"])
			if person:
				related = _accessible_records_for_person(person, context, policy["policy_version"])
				if related:
					accessible = related
		except Exception:
			# The direct record's service remains available when identity integration is unavailable.
			pass
	write_event(
		"Detail Open",
		context=context,
		portal_record_id=record_id,
		metadata={
			"view": "service_summary",
			"duration_ms": round((time.perf_counter() - started) * 1000, 2),
		},
	)
	return {"services": _service_names(accessible, service_name_fallback)}


@frappe.whitelist()
def same_person_records(record_id: str):
	"""Expand one selected record after a weak exact-name result, without a reason prompt."""
	require_post()
	started = time.perf_counter()
	context = _require("Detail Open", *CLIENT_AUTHORITIES, require_centres=True)
	try:
		access = ensure_record_access(record_id, context)
	except frappe.PermissionError:
		audit_denial("Detail Open", context=context, record_id=record_id)
		raise
	settings = frappe.get_single("CCD Portal Settings")
	policy, fields = get_active_policy()
	can_expand = bool(
		access.get("policy_version") == policy["policy_version"]
		and _same_person_state(context, policy, settings)["effective"]
	)
	result = _grouped_response(
		[access],
		context,
		fields,
		policy_version=policy["policy_version"],
		expand=can_expand,
		service_name_fallback=(
			getattr(settings, "service_name_fallback", None) or SERVICE_FALLBACK_UNSPECIFIED
		),
	)
	write_event(
		"Detail Open",
		context=context,
		portal_record_id=record_id,
		metadata={
			"same_person_expansion": True,
			"result_count": result["record_count"],
			"duration_ms": round((time.perf_counter() - started) * 1000, 2),
		},
	)
	return result


@frappe.whitelist()
def reveal(record_id: str, reason_code: str, context_note: str | None = None):
	require_post()
	context = _require("PII Reveal", *REVEAL_AUTHORITIES, require_centres=True)
	settings = frappe.get_single("CCD Portal Settings")
	_check_rate_limit(context, "reveal", settings.reveals_per_hour)
	if reason_code == CORRECTION_REVIEW_REASON or not _reason_exists(reason_code):
		audit_denial("PII Reveal", context=context, record_id=record_id, metadata={"stage": "reason"})
		frappe.throw(_("Select an approved reason for revealing this record."), frappe.ValidationError)
	try:
		access = ensure_record_access(record_id, context)
	except frappe.PermissionError:
		audit_denial("PII Reveal", context=context, record_id=record_id)
		raise
	policy, fields = get_active_policy()
	values = _record_document(access, fields)
	revealed = [
		{"fieldname": row["fieldname"], "label": row["label"], "value": values.get(row["fieldname"])}
		for row in fields
		if reveal_allowed(row, context.authority) and values.get(row["fieldname"]) is not None
	]
	person = None
	if (
		access.get("policy_version") == policy["policy_version"]
		and _same_person_state(context, policy, settings)["effective"]
	):
		try:
			person = person_for_record(access["ccd_master_record"])
		except Exception:
			person = None
	write_event(
		"PII Reveal",
		context=context,
		portal_record_id=record_id,
		reason_code=reason_code,
		protected_value=clean_context(context_note),
		metadata={
			"revealed_fields": [row["fieldname"] for row in revealed]
			+ (["unified_person_number"] if person else []),
		},
	)
	return {
		"id": record_id,
		"fields": revealed,
		"person_fields": _person_fields(person, revealed=True),
		"expires_in": cint(settings.reveal_ttl_seconds) or 120,
	}


@frappe.whitelist()
def submit_correction(record_id: str, changes=None, reason: str | None = None, centre: str | None = None):
	require_post()
	context = _require("Correction Submitted", "Operator", "Data Steward", require_centres=True)
	_require_correction_feature(context, "Correction Submitted", record_id)
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
def submit_identity_concern(record_ids=None, reason: str | None = None):
	"""Report a suspected wrong link without changing identity or access state."""
	require_post()
	context = _require("Identity Concern Submitted", "Operator", "Data Steward", require_centres=True)
	settings = _require_identity_concern_feature(context, "Identity Concern Submitted")
	_check_rate_limit(context, "identity-concern", getattr(settings, "concerns_per_hour", 10) or 10)
	reason = clean_reason(reason or "")
	record_ids = _json(record_ids, list)
	if not 2 <= len(record_ids) <= MAX_RESULTS or any(not isinstance(value, str) for value in record_ids):
		frappe.throw(_("Select between 2 and 20 visible records."), frappe.ValidationError)
	record_ids = [value.strip() for value in record_ids]
	if any(not value for value in record_ids) or len(set(record_ids)) != len(record_ids):
		frappe.throw(_("Select each visible record once."), frappe.ValidationError)
	policy, _ = get_active_policy()
	if not _same_person_state(context, policy, settings)["effective"]:
		frappe.throw(_("This grouped result is no longer available."), frappe.ValidationError)
	person = None
	accesses = []
	for record_id in record_ids:
		try:
			accesses.append(ensure_record_access(record_id, context))
		except frappe.PermissionError:
			audit_denial("Identity Concern Submitted", context=context, record_id=record_id)
			raise
	if any(access.get("policy_version") != policy["policy_version"] for access in accesses):
		frappe.throw(_("The selected grouped result is no longer current."), frappe.ValidationError)
	try:
		people = persons_for_records([access["ccd_master_record"] for access in accesses])
	except Exception:
		people = {}
	for access in accesses:
		current = people.get(access["ccd_master_record"])
		if not current or (person and current["name"] != person["name"]):
			frappe.throw(_("The selected grouped result is no longer current."), frappe.ValidationError)
		person = current
	context_payload = {"reason": reason, "record_ids": sorted(record_ids)}
	doc = frappe.get_doc(
		{
			"doctype": "CCD Portal Identity Concern",
			"status": "Submitted",
			"reporter": context.user,
			"reporter_authority": context.authority,
			"record_count": len(record_ids),
			"records": [{"portal_record_id": record_id} for record_id in sorted(record_ids)],
			"person_digest": protected_digest(person["name"], "identity-concern-person"),
			"encrypted_context": encrypt(canonical_json(context_payload)),
			"context_digest": protected_digest(canonical_json(context_payload), "identity-concern"),
		}
	).insert(ignore_permissions=True)
	write_event(
		"Identity Concern Submitted",
		context=context,
		protected_value=reason,
		metadata={"concern": doc.name, "record_count": len(record_ids)},
	)
	return {"concern_id": doc.name, "status": doc.status}


@frappe.whitelist()
def identity_concerns():
	context = _require("Identity Concern Status Opened", "Operator", "Data Steward", require_centres=True)
	_require_identity_concern_feature(context, "Identity Concern Status Opened")
	rows = frappe.get_all(
		"CCD Portal Identity Concern",
		filters={"reporter": context.user},
		fields=["name", "status", "creation", "modified"],
		order_by="creation desc",
		limit=100,
	)
	write_event(
		"Identity Concern Status Opened",
		context=context,
		metadata={"concern_count": len(rows)},
	)
	return {"concerns": [dict(row) for row in rows]}


def _identity_reviewer_context(event_type: str) -> PortalContext:
	user = getattr(frappe.session, "user", "Guest")
	roles = set(frappe.get_roles(user)) if user != "Guest" else set()
	if "System Manager" not in roles and "CCD Match Sensitive Reviewer" not in roles:
		audit_denial(event_type)
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	authority = "System Manager" if "System Manager" in roles else "CCD Match Sensitive Reviewer"
	return PortalContext(user=user, authority=authority, centres=())


@frappe.whitelist()
def transition_identity_concern(concern_id: str, status: str, reason: str):
	"""Apply one guided concern transition without changing identity state."""
	require_post()
	context = _identity_reviewer_context("Identity Concern Status Changed")
	reason = clean_reason(reason)
	status = str(status or "").strip()
	expected_status = {"In Review": "Submitted", "Resolved": "In Review", "Dismissed": "In Review"}
	if status not in expected_status:
		audit_denial(
			"Identity Concern Status Changed",
			context=context,
			metadata={"stage": "invalid_transition"},
		)
		frappe.throw(_("Select a valid concern review action."), frappe.ValidationError)
	if not frappe.db.exists("CCD Portal Identity Concern", concern_id):
		audit_denial("Identity Concern Status Changed", context=context)
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	doc = frappe.get_doc("CCD Portal Identity Concern", concern_id)
	if doc.status != expected_status[status]:
		audit_denial(
			"Identity Concern Status Changed",
			context=context,
			metadata={"stage": "stale_transition", "requested_status": status},
		)
		frappe.throw(_("Refresh the concern and use the action available for its current status."), frappe.ValidationError)
	doc.status = status
	doc.review_reason = reason
	doc.save(ignore_permissions=True)
	return {"concern_id": doc.name, "status": doc.status}


@frappe.whitelist()
def identity_concern_review_context(concern_id: str, reason: str):
	"""Reasoned, temporary Desk access for qualified identity reviewers."""
	require_post()
	context = _identity_reviewer_context("Identity Concern Review Context")
	reason = clean_reason(reason)
	settings = frappe.get_single("CCD Portal Settings")
	_check_rate_limit(context, "identity-concern-review", settings.reveals_per_hour)
	concern = frappe.db.get_value(
		"CCD Portal Identity Concern",
		concern_id,
		["name", "status", "encrypted_context"],
		as_dict=True,
	)
	if not concern:
		audit_denial("Identity Concern Review Context", context=context)
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
	try:
		payload = json.loads(decrypt(concern.encrypted_context))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CCD Portal concern decryption failure")
		frappe.throw(_("The concern context is unavailable."), frappe.ValidationError)
	record_ids = [str(value) for value in payload.get("record_ids", []) if str(value)]
	workflow_records = []
	comparison_fields = []
	if record_ids:
		placeholders = ", ".join(["%s"] * len(record_ids))
		rows = frappe.db.sql(
			f"""
			SELECT r.portal_record_id, r.ccd_master_record,
			       NULLIF(TRIM(c.service_name), '') AS record_service_name,
			       NULLIF(TRIM(registration.service_name), '') AS registration_service_name,
			       r.source_registration AS fallback_service_source
			  FROM `tabCCD Portal Record` r
			  JOIN `tabCCD Master` c ON c.name = r.ccd_master_record
			  LEFT JOIN `tabCCD Portal Source Profile` profile
			    ON profile.canonical_source_id = r.source_registration
			  LEFT JOIN `tabCCD Registration` registration
			    ON registration.name = profile.source_registration
			 WHERE r.portal_record_id IN ({placeholders})
			 ORDER BY r.portal_record_id
			""",
			record_ids,
			as_dict=True,
		)
		_, field_policies = get_active_policy()
		identity_fields = _identity_review_fields(field_policies)
		fieldnames = [row["fieldname"] for row in identity_fields]
		master_values = (
			{
				str(value.name): dict(value)
				for value in frappe.get_all(
					"CCD Master",
					filters={"name": ["in", [value.ccd_master_record for value in rows]]},
					fields=["name", *fieldnames],
					limit_page_length=len(rows),
				)
			}
			if rows
			else {}
		)
		service_name_fallback = (
			getattr(settings, "service_name_fallback", None) or SERVICE_FALLBACK_UNSPECIFIED
		)
		for source_row in rows:
			values = master_values.get(str(source_row.ccd_master_record), {})
			workflow_records.append(
				{
					"ccd_master_record": source_row.ccd_master_record,
					"service_name": resolve_service_name(
						source_row.record_service_name,
						source_row.registration_service_name,
						source_row.fallback_service_source,
						service_name_fallback,
					),
					"values": {
						fieldname: str(values.get(fieldname) or "") for fieldname in fieldnames
					},
				}
			)
		comparison_fields = [
			{
				"fieldname": row["fieldname"],
				"label": row["label"],
				"different": len(
					{record["values"].get(row["fieldname"], "") for record in workflow_records}
				)
				> 1,
			}
			for row in identity_fields
		]
	write_event(
		"Identity Concern Review Context",
		context=context,
		protected_value=reason,
		metadata={
			"concern": concern.name,
			"status": concern.status,
			"record_count": len(workflow_records),
			"fields": [field["fieldname"] for field in comparison_fields],
		},
	)
	return {
		"concern_id": concern.name,
		"status": concern.status,
		"reason": payload.get("reason", ""),
		"record_ids": record_ids,
		"workflow_records": workflow_records,
		"comparison_fields": comparison_fields,
		"expires_in": cint(settings.reveal_ttl_seconds) or 120,
	}


@frappe.whitelist()
def corrections():
	context = _require("Correction Queue Opened", "Operator", "Data Steward", require_centres=True)
	_require_correction_feature(context, "Correction Queue Opened")
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
	settings = _require_correction_feature(context, "Correction Proposal Reveal")
	_check_rate_limit(context, "reveal", settings.reveals_per_hour)
	if reason_code != CORRECTION_REVIEW_REASON or not _reason_exists(reason_code):
		audit_denial("Correction Proposal Reveal", context=context, metadata={"stage": "reason"})
		frappe.throw(_("Correction comparisons must use the correction review purpose."), frappe.ValidationError)
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
	_require_correction_feature(context, "Correction Decision")
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
		settings = frappe.get_single("CCD Portal Settings")
		result = _masked_record(
			access,
			fields,
			getattr(settings, "service_name_fallback", None) or SERVICE_FALLBACK_UNSPECIFIED,
		)
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
