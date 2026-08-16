from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from ccd_portal.audit import audit_denial, write_event
from ccd_portal.indexing import coverage_report, refresh_source
from ccd_portal.primitives import is_valid_source_column
from ccd_portal.security import (
	AUTHORITIES,
	clean_reason,
	require_access_administrator,
	require_post,
	require_system_manager,
)
from ccd_portal.source_identity import canonical_source_id, same_source_lineage

CENTRE_MAPPING_FIELDNAME = "ccd_portal_centre_key"
CENTRE_MAPPING_OPTION = "ccd_portal_centre_key: CCD Portal Canonical Centre Key"

RESOURCE_FIELDS = {
	"centres": (
		"CCD Portal Centre",
		("centre_code", "centre_name", "department", "active"),
		("name", "centre_code", "centre_name", "department", "active", "modified"),
	),
	"aliases": (
		"CCD Portal Centre Alias",
		("alias_code", "centre", "source_profile", "active"),
		("name", "alias_code", "centre", "source_profile", "active", "modified"),
	),
	"source_profiles": (
		"CCD Portal Source Profile",
		(
			"profile_code",
			"source_registration",
			"assignment_mode",
			"fixed_centres",
			"parser_type",
			"delimiter",
			"parser_pattern",
			"active",
		),
		(
			"name",
			"profile_code",
			"source_registration",
			"canonical_source_id",
			"assignment_mode",
			"parser_type",
			"delimiter",
			"parser_pattern",
			"sync_pending",
			"active",
		),
	),
	"profiles": (
		"CCD Portal User Profile",
		("user", "authority", "active"),
		("name", "user", "authority", "active", "modified"),
	),
	"grants": (
		"CCD Portal Centre Grant",
		("user", "centre", "active", "effective_from", "effective_to"),
		("name", "user", "centre", "active", "effective_from", "effective_to", "modified"),
	),
	"reveal_reasons": (
		"CCD Portal Reveal Reason",
		("reason_code", "label", "display_order", "active"),
		("name", "reason_code", "label", "display_order", "active", "modified"),
	),
}

IMMUTABLE_RESOURCE_FIELDS = {
	"centres": ("centre_code",),
	"source_profiles": ("profile_code", "source_registration"),
	"profiles": ("user",),
	"grants": ("user", "centre"),
	"reveal_reasons": ("reason_code",),
}


def _reference_option(value, label: str, **metadata):
	return {"value": value, "label": label, **metadata}


def _administrator_context(event_type: str):
	try:
		return require_access_administrator()
	except Exception:
		audit_denial(event_type)
		raise


def _resource(resource: str):
	if resource not in RESOURCE_FIELDS:
		frappe.throw(_("Unsupported administration resource."), frappe.ValidationError)
	return RESOURCE_FIELDS[resource]


def _invalidate_source_relations(source_registration: str) -> None:
	"""Fail closed after a source assignment changes, until its index is refreshed."""
	source_id = canonical_source_id(source_registration)
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Record Centre` rc
		JOIN `tabCCD Portal Record` r ON r.name = rc.portal_record
		   SET rc.active = 0
		 WHERE r.source_registration = %s AND rc.active = 1
		""",
		(source_id,),
	)


def _is_centre_mapping_target(value: str | None) -> bool:
	return str(value or "").partition(":")[0].strip() == CENTRE_MAPPING_FIELDNAME


def _centre_mapping_rows(source_registration: str) -> list[dict]:
	rows = frappe.get_all(
		"CCD Field Match",
		filters={
			"parent": source_registration,
			"parenttype": "CCD Registration",
			"parentfield": "fieldmatch",
		},
		fields=["name", "ccd_fieldname", "sys_fieldname", "fieldtype", "assignment", "idx"],
		order_by="idx asc",
	)
	return [dict(row) for row in rows if _is_centre_mapping_target(row.sys_fieldname)]


def _centre_key_coverage(source_registration: str) -> dict:
	source_id = canonical_source_id(source_registration)
	row = frappe.db.sql(
		"""
		SELECT COUNT(*) AS total,
		       SUM(CASE WHEN COALESCE(TRIM(ccd_portal_centre_key), '') <> '' THEN 1 ELSE 0 END) AS keyed
		  FROM `tabCCD Master`
		 WHERE ccd_reg_source = %s
		""",
		(source_id,),
		as_dict=True,
	)[0]
	return {"total": int(row.total or 0), "keyed": int(row.keyed or 0)}


def _mapping_status(row: dict) -> dict:
	if row.get("assignment_mode") == "Fixed Centres":
		return {
			"authoritative_centre_column": "",
			"mapping_status": _("Not required (fixed centres)"),
			"centre_key_total": 0,
			"centre_key_populated": 0,
		}
	rows = _centre_mapping_rows(row["source_registration"])
	coverage = _centre_key_coverage(row["source_registration"])
	if len(rows) > 1:
		status = _("Invalid duplicate centre mappings")
	elif not rows or not str(rows[0].get("ccd_fieldname") or "").strip():
		status = _("Centre column not mapped")
	elif row.get("sync_pending") or coverage["keyed"] < coverage["total"] or coverage["total"] == 0:
		status = _("Waiting for source sync ({0}/{1})").format(coverage["keyed"], coverage["total"])
	else:
		status = _("Ready ({0}/{1})").format(coverage["keyed"], coverage["total"])
	return {
		"authoritative_centre_column": str(rows[0].get("ccd_fieldname") or "").strip() if len(rows) == 1 else "",
		"mapping_status": status,
		"centre_key_total": coverage["total"],
		"centre_key_populated": coverage["keyed"],
	}


def _set_centre_mapping(source_registration: str, source_column: str) -> bool:
	rows = _centre_mapping_rows(source_registration)
	if len(rows) > 1:
		frappe.throw(_("The submitted registration has duplicate canonical centre mappings."), frappe.ValidationError)
	if rows and str(rows[0].get("ccd_fieldname") or "").strip() == source_column:
		return False
	values = {
		"ccd_fieldname": source_column,
		"sys_fieldname": CENTRE_MAPPING_OPTION,
		"fieldtype": "Data",
		"assignment": "",
	}
	if rows:
		frappe.db.set_value("CCD Field Match", rows[0]["name"], values, update_modified=False)
	else:
		idx = frappe.db.sql(
			"SELECT COALESCE(MAX(idx), 0) FROM `tabCCD Field Match` WHERE parent = %s AND parenttype = 'CCD Registration' AND parentfield = 'fieldmatch'",
			(source_registration,),
		)[0][0]
		child = frappe.get_doc(
			{
				"doctype": "CCD Field Match",
				"parent": source_registration,
				"parenttype": "CCD Registration",
				"parentfield": "fieldmatch",
				"idx": int(idx or 0) + 1,
				**values,
			}
		)
		child.db_insert()
	frappe.db.set_value(
		"CCD Registration",
		source_registration,
		{"modified": now_datetime(), "modified_by": frappe.session.user},
		update_modified=False,
	)
	return True


def _clear_centre_keys(source_registration: str) -> int:
	source_id = canonical_source_id(source_registration)
	count = frappe.db.count("CCD Master", {"ccd_reg_source": source_id})
	frappe.db.sql(
		"UPDATE `tabCCD Master` SET ccd_portal_centre_key = NULL WHERE ccd_reg_source = %s",
		(source_id,),
	)
	return int(count or 0)


def _strict_refresh(source_registration: str, reason: str, context) -> dict:
	result = refresh_source(source_registration, strict=True)
	if result["unmapped"] or result["failed"] or result["indexed"] != result["total"]:
		frappe.throw(
			_("The source remains disabled because every record could not be indexed. Complete synchronization and check centre aliases."),
			frappe.ValidationError,
		)
	write_event(
		"Index Refresh",
		context=context,
		protected_value={"source": source_registration, "reason": reason},
		metadata={**result, "strict_source_gate": True},
	)
	return result


@frappe.whitelist()
def list_resources(resource: str):
	context = _administrator_context("Administration Read")
	doctype, _, response_fields = _resource(resource)
	rows = frappe.get_all(doctype, fields=list(response_fields), order_by="modified desc", limit=500)
	if resource == "source_profiles":
		for row in rows:
			row["assignment_mode"] = row.assignment_mode or "Per-record Centre Key"
			row["fixed_centres"] = frappe.get_all(
				"CCD Portal Source Fixed Centre",
				filters={"parent": row.name, "parenttype": doctype, "parentfield": "fixed_centres"},
				pluck="centre",
				order_by="idx asc",
			)
			row["fixed_centres_display"] = ", ".join(row.fixed_centres) or "—"
			row.update(_mapping_status(row))
	write_event("Administration Read", context=context, metadata={"resource": resource})
	return {"resource": resource, "rows": [dict(row) for row in rows]}


@frappe.whitelist()
def reference_options():
	"""Return narrow governance references for accessible administration selects."""
	context = _administrator_context("Administration Reference Read")
	centres = frappe.get_all(
		"CCD Portal Centre",
		fields=["name", "centre_code", "centre_name", "active"],
		order_by="centre_name asc, centre_code asc",
		limit=500,
	)
	sources = frappe.get_all(
		"CCD Portal Source Profile",
		fields=["name", "profile_code", "source_registration", "active"],
		order_by="profile_code asc",
		limit=500,
	)
	profiles = frappe.get_all(
		"CCD Portal User Profile",
		fields=["name", "user", "authority", "active"],
		order_by="user asc",
		limit=500,
	)
	registrations = frappe.get_all(
		"CCD Registration",
		filters={"docstatus": 1},
		fields=["name", "app_name"],
		order_by="name asc",
		limit=500,
	)
	registration_names = [row.name for row in registrations]
	registration_columns = (
		frappe.get_all(
			"CCD Field Match",
			filters={
				"parent": ["in", registration_names],
				"parenttype": "CCD Registration",
				"parentfield": "fieldmatch",
				"ccd_fieldname": ["is", "set"],
			},
			fields=["parent", "ccd_fieldname", "fieldtype"],
			order_by="parent asc, idx asc",
			limit=5000,
		)
		if registration_names
		else []
	)
	users = frappe.get_all(
		"User",
		filters={"enabled": 1, "user_type": "System User"},
		fields=["name", "full_name"],
		order_by="full_name asc, name asc",
		limit=500,
	)
	departments = frappe.get_all(
		"Department",
		fields=["name", "department_name", "disabled"],
		order_by="department_name asc, name asc",
		limit=500,
	)
	result = {
		"centres": [
			_reference_option(
				row.name,
				f"{row.centre_name} — {row.centre_code}",
				active=bool(row.active),
			)
			for row in centres
		],
		"source_profiles": [
			_reference_option(
				row.name,
				f"{row.profile_code} — {row.source_registration}",
				active=bool(row.active),
			)
			for row in sources
		],
		"profiles": [
			_reference_option(
				row.user,
				f"{row.user} — {row.authority}",
				active=bool(row.active),
			)
			for row in profiles
		],
		"registrations": [
			_reference_option(row.name, f"{row.name} — {row.app_name}" if row.app_name else row.name)
			for row in registrations
		],
		"registration_columns": [
			_reference_option(
				row.ccd_fieldname,
				row.ccd_fieldname,
				registration=row.parent,
				fieldtype=row.fieldtype or "Data",
			)
			for row in registration_columns
		],
		"users": [
			_reference_option(
				row.name,
				f"{row.full_name} — {row.name}" if row.full_name and row.full_name != row.name else row.name,
			)
			for row in users
		],
		"departments": [
			_reference_option(
				row.name,
				row.department_name or row.name,
				active=not bool(row.disabled),
			)
			for row in departments
		],
	}
	write_event(
		"Administration Read",
		context=context,
		metadata={"resource": "reference_options"},
	)
	return result


@frappe.whitelist()
def upsert_resource(resource: str, values=None, name: str | None = None):
	require_post()
	context = _administrator_context("Administration Change")
	doctype, allowed_fields, response_fields = _resource(resource)
	values = frappe.parse_json(values) if isinstance(values, str) else values
	if not isinstance(values, dict) or set(values) - set(allowed_fields):
		frappe.throw(_("Invalid administration values."), frappe.ValidationError)
	audit_values = dict(values)
	fixed_centres = None
	if resource == "source_profiles":
		fixed_centres = values.pop("fixed_centres", [])
		if not isinstance(fixed_centres, list) or any(not isinstance(centre, str) for centre in fixed_centres):
			frappe.throw(_("Select valid fixed centres."), frappe.ValidationError)
		fixed_centres = [centre.strip() for centre in fixed_centres if centre.strip()]
	if resource == "profiles" and values.get("authority") not in AUTHORITIES:
		frappe.throw(_("Select exactly one valid authority."), frappe.ValidationError)
	if name:
		doc = frappe.get_doc(doctype, name)
		for fieldname in IMMUTABLE_RESOURCE_FIELDS.get(resource, ()):
			if fieldname not in values or str(values[fieldname] or "") == str(doc.get(fieldname) or ""):
				continue
			if (
				resource == "source_profiles"
				and fieldname == "source_registration"
				and same_source_lineage(values[fieldname], doc.get(fieldname))
			):
				continue
			frappe.throw(_("Create a new record instead of changing its governed identity."))
		doc.update(values)
		if resource == "source_profiles":
			doc.set("fixed_centres", [{"centre": centre} for centre in fixed_centres])
		doc.save(ignore_permissions=True)
		action = "Updated"
	else:
		doc = frappe.get_doc({"doctype": doctype, **values})
		if resource == "source_profiles":
			doc.set("fixed_centres", [{"centre": centre} for centre in fixed_centres])
		doc.insert(ignore_permissions=True)
		action = "Created"
	if resource == "source_profiles":
		_invalidate_source_relations(doc.source_registration)
	write_event(
		"Administration Change",
		context=context,
		protected_value=audit_values,
		metadata={
			"resource": resource,
			"action": action,
			"index_refresh_required": resource == "source_profiles",
		},
	)
	result = {field: doc.get(field) for field in response_fields}
	if resource == "source_profiles":
		result["fixed_centres"] = [row.centre for row in doc.fixed_centres]
		result["fixed_centres_display"] = ", ".join(result["fixed_centres"]) or "—"
		result["canonical_source_id"] = doc.canonical_source_id
	return result


@frappe.whitelist()
def upsert_source_profile_and_refresh(values=None, name: str | None = None, reason: str = ""):
	"""Atomically save one source assignment and rebuild its governed index."""
	require_post()
	require_system_manager(reason)
	reason = clean_reason(reason)
	result = upsert_resource("source_profiles", values, name)
	frappe.db.set_value("CCD Portal Source Profile", result["name"], "sync_pending", 0)
	refresh = refresh_index(reason, result["source_registration"], _strict=True)
	if refresh["unmapped"] or refresh["failed"]:
		frappe.throw(
			_("The source assignment was not saved because its index could not be rebuilt completely."),
			frappe.ValidationError,
		)
	return {"source_profile": result, "refresh": refresh}


@frappe.whitelist()
def configure_source(values=None, name: str | None = None, authoritative_centre_column: str = "", reason: str = ""):
	"""Save a fixed or per-record source configuration through one governed UI path.

	A changed submitted-registration mapping enters a fail-closed synchronization
	gate. The existing agent will read the new child mapping on its next full sync;
	portal relations cannot be recreated until ``complete_source_sync`` succeeds.
	"""
	require_post()
	_administrator_context("Source Configuration Change")
	context = require_system_manager(reason)
	reason = clean_reason(reason)
	values = frappe.parse_json(values) if isinstance(values, str) else values
	if not isinstance(values, dict):
		frappe.throw(_("Invalid source configuration."), frappe.ValidationError)
	values = dict(values)
	source_registration = str(values.get("source_registration") or "").strip()
	if not source_registration or frappe.db.get_value("CCD Registration", source_registration, "docstatus") != 1:
		frappe.throw(_("Select a submitted CCD Registration."), frappe.ValidationError)

	if values.get("assignment_mode") == "Fixed Centres":
		result = upsert_source_profile_and_refresh(values, name, reason)
		frappe.db.set_value("CCD Portal Source Profile", result["source_profile"]["name"], "sync_pending", 0)
		return {**result, "sync_required": False}

	if values.get("assignment_mode") != "Per-record Centre Key":
		frappe.throw(_("Select a valid centre-assignment mode."), frappe.ValidationError)
	source_column = str(authoritative_centre_column or "").strip()
	if not is_valid_source_column(source_column):
		frappe.throw(
			_("Enter an exact source column using only letters, numbers, and underscores; it cannot begin with a number."),
			frappe.ValidationError,
		)
	values["fixed_centres"] = []
	prior_assignment = (
		frappe.db.get_value("CCD Portal Source Profile", name, "assignment_mode") if name else None
	)
	mapping_changed = _set_centre_mapping(source_registration, source_column)
	requires_fresh_sync = mapping_changed or prior_assignment != "Per-record Centre Key"
	cleared = _clear_centre_keys(source_registration) if requires_fresh_sync else 0
	coverage = _centre_key_coverage(source_registration)

	if requires_fresh_sync or coverage["total"] == 0 or coverage["keyed"] != coverage["total"]:
		result = upsert_resource("source_profiles", values, name)
		frappe.db.set_value("CCD Portal Source Profile", result["name"], "sync_pending", 1)
		_invalidate_source_relations(source_registration)
		write_event(
			"Centre Mapping Change" if mapping_changed else "Source Sync Pending",
			context=context,
			protected_value={
				"source": source_registration,
				"source_column": source_column,
				"reason": reason,
			},
			metadata={
				"mapping_changed": mapping_changed,
				"assignment_changed": prior_assignment != "Per-record Centre Key",
				"cleared_record_count": cleared,
				"keyed_records": coverage["keyed"],
				"total_records": coverage["total"],
				"sync_required": True,
			},
		)
		return {
			"source_profile": result,
			"sync_required": True,
			"keyed_records": coverage["keyed"],
			"total_records": coverage["total"],
		}

	result = upsert_resource("source_profiles", values, name)
	frappe.db.set_value("CCD Portal Source Profile", result["name"], "sync_pending", 0)
	refresh = _strict_refresh(source_registration, reason, context)
	return {"source_profile": result, "refresh": refresh, "sync_required": False}


@frappe.whitelist()
def complete_source_sync(source: str, reason: str):
	"""Open a pending per-record source only after complete key coverage and indexing."""
	require_post()
	_administrator_context("Source Sync Completion")
	context = require_system_manager(reason)
	reason = clean_reason(reason)
	profile = frappe.db.get_value(
		"CCD Portal Source Profile",
		{"source_registration": source, "active": 1},
		["name", "assignment_mode", "sync_pending"],
		as_dict=True,
	)
	if not profile or profile.assignment_mode != "Per-record Centre Key":
		frappe.throw(_("Select an active per-record source assignment."), frappe.ValidationError)
	if len(_centre_mapping_rows(source)) != 1:
		frappe.throw(_("Exactly one authoritative centre-column mapping is required."), frappe.ValidationError)
	coverage = _centre_key_coverage(source)
	if coverage["total"] == 0 or coverage["keyed"] != coverage["total"]:
		frappe.throw(
			_("Source synchronization is incomplete: {0} of {1} records contain a centre key.").format(
				coverage["keyed"], coverage["total"]
			),
			frappe.ValidationError,
		)
	frappe.db.set_value("CCD Portal Source Profile", profile.name, "sync_pending", 0)
	refresh = _strict_refresh(source, reason, context)
	write_event(
		"Source Sync Completed",
		context=context,
		protected_value={"source": source, "reason": reason},
		metadata={**coverage, "indexed": refresh["indexed"]},
	)
	return {"source": source, "refresh": refresh, **coverage, "sync_pending": False}


@frappe.whitelist()
def list_policies():
	context = _administrator_context("Administration Read")
	rows = frappe.get_all(
		"CCD Portal Policy",
		fields=["name", "policy_version", "title", "status", "activated_on", "modified"],
		order_by="modified desc",
	)
	field_names = [
		"fieldname",
		"label",
		"classification",
		"data_kind",
		"mask_strategy",
		"reveal_authorities",
		"searchable",
		"strong_identifier",
		"correctable",
		"display_order",
	]
	for row in rows:
		row["fields"] = frappe.get_all(
			"CCD Portal Field Policy",
			filters={"parent": row.name, "parenttype": "CCD Portal Policy", "parentfield": "fields"},
			fields=field_names,
			order_by="idx asc",
		)
	write_event("Administration Read", context=context, metadata={"resource": "policies"})
	return {"policies": [dict(row) for row in rows]}


@frappe.whitelist()
def save_draft_policy(policy_version: str, title: str, fields=None, name: str | None = None):
	require_post()
	context = _administrator_context("Policy Draft Change")
	fields = frappe.parse_json(fields) if isinstance(fields, str) else fields
	if not isinstance(fields, list) or not fields:
		frappe.throw(_("A policy requires field rules."), frappe.ValidationError)
	allowed = {
		"fieldname",
		"label",
		"classification",
		"data_kind",
		"mask_strategy",
		"reveal_authorities",
		"searchable",
		"strong_identifier",
		"correctable",
		"display_order",
	}
	if any(not isinstance(row, dict) or set(row) - allowed for row in fields):
		frappe.throw(_("Invalid policy rule."), frappe.ValidationError)
	if name:
		doc = frappe.get_doc("CCD Portal Policy", name)
		if doc.status != "Draft":
			frappe.throw(_("Activated policies are immutable."), frappe.PermissionError)
		doc.title = title
		doc.set("fields", fields)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "CCD Portal Policy",
				"policy_version": policy_version,
				"title": title,
				"status": "Draft",
				"fields": fields,
			}
		).insert(ignore_permissions=True)
	write_event(
		"Policy Draft Change",
		context=context,
		protected_value=fields,
		metadata={"policy": doc.name, "version": doc.policy_version},
	)
	return {"name": doc.name, "policy_version": doc.policy_version, "status": doc.status}


@frappe.whitelist()
def activate_policy(policy_name: str, reason: str):
	require_post()
	context = require_system_manager(reason)
	reason = clean_reason(reason)
	doc = frappe.get_doc("CCD Portal Policy", policy_name)
	if doc.status != "Draft":
		frappe.throw(_("Only a draft policy can be activated."), frappe.ValidationError)
	for active_name in frappe.get_all("CCD Portal Policy", filters={"status": "Active"}, pluck="name"):
		frappe.db.set_value("CCD Portal Policy", active_name, "status", "Retired", update_modified=False)
	frappe.db.set_value(
		"CCD Portal Policy",
		doc.name,
		{"status": "Active", "activated_by": context.user, "activated_on": now_datetime()},
		update_modified=False,
	)
	write_event(
		"Policy Activated",
		context=context,
		protected_value=reason,
		metadata={"policy": doc.name, "version": doc.policy_version},
	)
	return {"name": doc.name, "status": "Active"}


@frappe.whitelist()
def get_coverage():
	context = _administrator_context("Coverage Report Opened")
	report = coverage_report()
	write_event("Coverage Report Opened", context=context, metadata={"coverage_percent": report["coverage_percent"]})
	return report


@frappe.whitelist()
def refresh_index(reason: str, source: str | None = None, source_keys=None, _strict: bool = False):
	require_post()
	context = require_system_manager(reason)
	reason = clean_reason(reason)
	if source_keys:
		source_keys = frappe.parse_json(source_keys) if isinstance(source_keys, str) else source_keys
		if not isinstance(source_keys, list) or len(source_keys) > 1000:
			frappe.throw(_("Invalid source key batch."), frappe.ValidationError)
	result = refresh_source(source, source_keys, strict=bool(_strict))
	write_event(
		"Index Refresh",
		context=context,
		protected_value={"source": source, "source_keys": source_keys or []},
		metadata={**result, "reason_digest_recorded": True},
	)
	return result
