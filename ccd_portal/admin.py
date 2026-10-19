from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ccd_portal.audit import audit_denial, write_event
from ccd_portal.identity import identity_coverage, schema_status
from ccd_portal.indexing import coverage_report, refresh_source
from ccd_portal.install import ensure_settings_defaults
from ccd_portal.policy import get_active_policy
from ccd_portal.primitives import is_valid_source_column
from ccd_portal.security import (
	AUTHORITIES,
	clean_reason,
	require_access_administrator,
	require_post,
	require_system_manager,
)
from ccd_portal.source_identity import canonical_source_id, same_source_lineage
from ccd_portal.source_refresh import (
	assert_source_refresh_available,
	disable_source_relations,
	operation_for_source,
	queue_source_refresh,
)

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
	disable_source_relations(source_registration)


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
			row.update(operation_for_source(row.source_registration, context.user))
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
				authority=row.authority,
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
		frappe.db.set_value("CCD Portal Source Profile", doc.name, "sync_pending", 1)
		doc.sync_pending = 1
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
	"""Save one source assignment and queue its fail-closed governed rebuild."""
	require_post()
	context = require_system_manager(reason)
	reason = clean_reason(reason)
	parsed_values = frappe.parse_json(values) if isinstance(values, str) else values
	if not isinstance(parsed_values, dict):
		frappe.throw(_("Invalid source configuration."), frappe.ValidationError)
	assert_source_refresh_available(str(parsed_values.get("source_registration") or ""))
	result = upsert_resource("source_profiles", values, name)
	refresh_operation = queue_source_refresh(
		profile_name=result["name"],
		source=result["source_registration"],
		reason=reason,
		context=context,
	)
	return {"source_profile": result, "refresh_operation": refresh_operation}


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
	assert_source_refresh_available(source_registration)

	if values.get("assignment_mode") == "Fixed Centres":
		result = upsert_source_profile_and_refresh(values, name, reason)
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
	refresh_operation = queue_source_refresh(
		profile_name=result["name"],
		source=source_registration,
		reason=reason,
		context=context,
	)
	return {
		"source_profile": result,
		"refresh_operation": refresh_operation,
		"sync_required": False,
	}


@frappe.whitelist()
def complete_source_sync(source: str, reason: str):
	"""Queue opening a pending per-record source after complete key coverage."""
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
	refresh_operation = queue_source_refresh(
		profile_name=profile.name,
		source=source,
		reason=reason,
		context=context,
		completion_event="Source Sync Completed",
	)
	return {
		"source": source,
		"refresh_operation": refresh_operation,
		**coverage,
		"sync_pending": True,
	}


@frappe.whitelist()
def list_policies():
	context = _administrator_context("Administration Read")
	rows = frappe.get_all(
		"CCD Portal Policy",
		fields=["name", "policy_version", "title", "same_person_enabled", "status", "activated_on", "modified"],
		order_by="modified desc",
	)
	field_names = [
		"source",
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
def save_draft_policy(
	policy_version: str,
	title: str,
	fields=None,
	name: str | None = None,
	same_person_enabled: int = 0,
):
	require_post()
	context = _administrator_context("Policy Draft Change")
	fields = frappe.parse_json(fields) if isinstance(fields, str) else fields
	if not isinstance(fields, list) or not fields:
		frappe.throw(_("A policy requires field rules."), frappe.ValidationError)
	allowed = {
		"source",
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
	for row in fields:
		row["source"] = row.get("source") or "CCD Master"
	if name:
		doc = frappe.get_doc("CCD Portal Policy", name)
		if doc.status != "Draft":
			frappe.throw(_("Activated policies are immutable."), frappe.PermissionError)
		doc.title = title
		doc.same_person_enabled = cint(same_person_enabled)
		doc.set("fields", fields)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "CCD Portal Policy",
				"policy_version": policy_version,
				"title": title,
				"same_person_enabled": cint(same_person_enabled),
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
def clone_active_policy_to_v2(policy_version: str, title: str):
	"""Create an audited V2 draft without mutating the active V1 policy."""
	require_post()
	context = _administrator_context("Policy V2 Clone")
	policy_version = str(policy_version or "").strip()
	title = str(title or "").strip()
	if not policy_version or len(policy_version) > 140 or not title or len(title) > 140:
		frappe.throw(_("Enter a valid V2 policy version and title."), frappe.ValidationError)
	if frappe.db.exists("CCD Portal Policy", policy_version):
		frappe.throw(_("That policy version already exists."), frappe.ValidationError)
	active, fields = get_active_policy()
	cloned_fields = [
		{
			**row,
			"source": row.get("source") or "CCD Master",
		}
		for row in fields
		if (row.get("source") or "CCD Master") == "CCD Master"
	]
	cloned_fields.append(
		{
			"source": "Unified Person",
			"fieldname": "unified_person_number",
			"label": "Unified Person Number",
			"classification": "Identity",
			"data_kind": "Identifier",
			"mask_strategy": "Last 4",
			"reveal_authorities": "Operator,Data Steward",
			"searchable": 1,
			"strong_identifier": 1,
			"correctable": 0,
			"display_order": max((cint(row.get("display_order")) for row in cloned_fields), default=0) + 10,
		}
	)
	doc = frappe.get_doc(
		{
			"doctype": "CCD Portal Policy",
			"policy_version": policy_version,
			"title": title,
			"same_person_enabled": 1,
			"status": "Draft",
			"fields": cloned_fields,
		}
	).insert(ignore_permissions=True)
	write_event(
		"Policy V2 Clone",
		context=context,
		metadata={"source_policy": active["name"], "policy": doc.name, "version": doc.policy_version},
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
	if doc.same_person_enabled and not schema_status()["ready"]:
		frappe.throw(
			_("The governed identity schema and indexes must be ready before activating a V2 policy."),
			frappe.ValidationError,
		)
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
	active = frappe.get_all(
		"CCD Portal Policy",
		filters={"status": "Active"},
		fields=["name", "same_person_enabled"],
		limit=2,
	)
	policy = dict(active[0]) if len(active) == 1 else {"same_person_enabled": 0}
	settings = frappe.get_single("CCD Portal Settings")
	report["same_person"] = identity_coverage(settings, policy)
	write_event("Coverage Report Opened", context=context, metadata={"coverage_percent": report["coverage_percent"]})
	return report


@frappe.whitelist()
def get_same_person_rollout():
	context = _administrator_context("Same Person Rollout Read")
	settings = frappe.get_single("CCD Portal Settings")
	result = {
		"global_enabled": bool(cint(settings.same_person_enabled)),
		"pilot_users": sorted(row.user for row in (settings.same_person_pilot_users or []) if row.user),
		"identity_schema": schema_status(),
	}
	write_event(
		"Same Person Rollout Read",
		context=context,
		metadata={
			"global_enabled": result["global_enabled"],
			"pilot_count": len(result["pilot_users"]),
			"schema_ready": result["identity_schema"]["ready"],
		},
	)
	return result


@frappe.whitelist()
def set_same_person_pilots(users=None):
	require_post()
	context = _administrator_context("Same Person Pilot Change")
	ensure_settings_defaults()
	users = frappe.parse_json(users) if isinstance(users, str) else users
	if not isinstance(users, list) or len(users) > 200:
		frappe.throw(_("Select a valid pilot-user list."), frappe.ValidationError)
	users = sorted({str(user or "").strip() for user in users if str(user or "").strip()})
	for user in users:
		profile = frappe.db.get_value(
			"CCD Portal User Profile", {"user": user, "active": 1}, "authority"
		)
		if profile not in {"Reader", "Operator", "Data Steward"}:
			frappe.throw(_("Every same-person pilot must have an active client-data profile."), frappe.ValidationError)
	settings = frappe.get_single("CCD Portal Settings")
	settings.set("same_person_pilot_users", [{"user": user} for user in users])
	settings.flags.same_person_governance = True
	settings.save(ignore_permissions=True)
	write_event(
		"Same Person Pilot Change",
		context=context,
		protected_value=users,
		metadata={"pilot_count": len(users)},
	)
	return {"pilot_users": users}


@frappe.whitelist()
def set_same_person_global(enabled: int, reason: str):
	"""System Manager activation is separate from Access Administrator pilots."""
	require_post()
	try:
		context = require_system_manager(reason)
	except Exception:
		audit_denial("Same Person Global Change")
		raise
	reason = clean_reason(reason)
	enabled = bool(cint(enabled))
	if enabled:
		policy, _ = get_active_policy()
		if not policy.get("same_person_enabled"):
			frappe.throw(_("Activate a V2 same-person policy first."), frappe.ValidationError)
		if not schema_status()["ready"]:
			frappe.throw(_("The governed identity schema and indexes are not ready."), frappe.ValidationError)
	frappe.db.set_single_value("CCD Portal Settings", "same_person_enabled", int(enabled))
	write_event(
		"Same Person Global Change",
		context=context,
		protected_value=reason,
		metadata={"enabled": enabled},
	)
	return {"global_enabled": enabled}


@frappe.whitelist()
def refresh_index(reason: str, source: str | None = None, source_keys=None, _strict: bool = False):
	require_post()
	context = require_system_manager(reason)
	reason = clean_reason(reason)
	if source_keys:
		source_keys = frappe.parse_json(source_keys) if isinstance(source_keys, str) else source_keys
		if not isinstance(source_keys, list) or len(source_keys) > 1000:
			frappe.throw(_("Invalid source key batch."), frappe.ValidationError)
	if source and not source_keys and not _strict:
		profile = frappe.db.get_value(
			"CCD Portal Source Profile",
			{"source_registration": source, "active": 1},
			"name",
		)
		if not profile:
			frappe.throw(_("Select an active source assignment."), frappe.ValidationError)
		return queue_source_refresh(
			profile_name=profile,
			source=source,
			reason=reason,
			context=context,
		)
	result = refresh_source(source, source_keys, strict=bool(_strict))
	write_event(
		"Index Refresh",
		context=context,
		protected_value={"source": source, "source_keys": source_keys or []},
		metadata={**result, "reason_digest_recorded": True},
	)
	return result
