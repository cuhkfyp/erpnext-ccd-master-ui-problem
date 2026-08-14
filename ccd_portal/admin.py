from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from ccd_portal.audit import audit_denial, write_event
from ccd_portal.indexing import coverage_report, refresh_source
from ccd_portal.security import (
	AUTHORITIES,
	clean_reason,
	require_access_administrator,
	require_post,
	require_system_manager,
)

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
		("profile_code", "source_registration", "parser_type", "delimiter", "parser_pattern", "active"),
		("name", "profile_code", "source_registration", "parser_type", "delimiter", "parser_pattern", "active"),
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


@frappe.whitelist()
def list_resources(resource: str):
	context = _administrator_context("Administration Read")
	doctype, _, response_fields = _resource(resource)
	rows = frappe.get_all(doctype, fields=list(response_fields), order_by="modified desc", limit=500)
	write_event("Administration Read", context=context, metadata={"resource": resource})
	return {"resource": resource, "rows": [dict(row) for row in rows]}


@frappe.whitelist()
def upsert_resource(resource: str, values=None, name: str | None = None):
	require_post()
	context = _administrator_context("Administration Change")
	doctype, allowed_fields, response_fields = _resource(resource)
	values = frappe.parse_json(values) if isinstance(values, str) else values
	if not isinstance(values, dict) or set(values) - set(allowed_fields):
		frappe.throw(_("Invalid administration values."), frappe.ValidationError)
	if resource == "profiles" and values.get("authority") not in AUTHORITIES:
		frappe.throw(_("Select exactly one valid authority."), frappe.ValidationError)
	if name:
		doc = frappe.get_doc(doctype, name)
		for fieldname in IMMUTABLE_RESOURCE_FIELDS.get(resource, ()):
			if fieldname in values and str(values[fieldname] or "") != str(doc.get(fieldname) or ""):
				frappe.throw(_("Create a new record instead of changing its governed identity."))
		doc.update(values)
		doc.save(ignore_permissions=True)
		action = "Updated"
	else:
		doc = frappe.get_doc({"doctype": doctype, **values})
		doc.insert(ignore_permissions=True)
		action = "Created"
	write_event(
		"Administration Change",
		context=context,
		protected_value=values,
		metadata={"resource": resource, "action": action},
	)
	return {field: doc.get(field) for field in response_fields}


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
def refresh_index(reason: str, source: str | None = None, source_keys=None):
	require_post()
	context = require_system_manager(reason)
	reason = clean_reason(reason)
	if source_keys:
		source_keys = frappe.parse_json(source_keys) if isinstance(source_keys, str) else source_keys
		if not isinstance(source_keys, list) or len(source_keys) > 1000:
			frappe.throw(_("Invalid source key batch."), frappe.ValidationError)
	result = refresh_source(source, source_keys)
	write_event(
		"Index Refresh",
		context=context,
		protected_value={"source": source, "source_keys": source_keys or []},
		metadata={**result, "reason_digest_recorded": True},
	)
	return result
