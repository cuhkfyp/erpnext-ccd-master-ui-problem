from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.password import decrypt, encrypt

from ccd_portal.security import PortalContext, canonical_json, protected_digest, require_post


def write_event(
	event_type: str,
	*,
	outcome: str = "Allowed",
	context=None,
	portal_record_id: str | None = None,
	criteria_types: list[str] | None = None,
	protected_value: Any = None,
	reason_code: str | None = None,
	user_context: str | None = None,
	metadata: dict | None = None,
) -> str:
	"""Write an audit event. Callers intentionally fail if this insert fails."""
	user = context.user if context else getattr(frappe.session, "user", "Guest")
	authority = context.authority if context else "Unauthenticated"
	digest = None
	if protected_value is not None:
		digest = protected_digest(canonical_json(protected_value), "audit-event")
	user_context = str(user_context or "").strip()

	doc = frappe.get_doc(
		{
			"doctype": "CCD Portal Audit Event",
			"event_type": event_type,
			"actor": user,
			"authority": authority,
			"outcome": outcome,
			"portal_record_id": portal_record_id,
			"criteria_types": canonical_json(sorted(criteria_types or [])),
			"protected_digest": digest,
			"reason_code": reason_code,
			"has_user_context": 1 if user_context else 0,
			"encrypted_user_context": encrypt(user_context) if user_context else None,
			"request_id": getattr(frappe.local, "request_id", None),
			"event_metadata": canonical_json(metadata or {}),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def view_user_context(event_name: str):
	"""Temporarily reveal a user-entered audit justification to a System Manager."""
	require_post()
	user = getattr(frappe.session, "user", "Guest")
	if user == "Guest" or "System Manager" not in set(frappe.get_roles(user)):
		audit_denial("Audit Justification Viewed")
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)

	event = frappe.db.get_value(
		"CCD Portal Audit Event",
		event_name,
		["name", "has_user_context", "encrypted_user_context"],
		as_dict=True,
	)
	if not event or not cint(event.has_user_context) or not event.encrypted_user_context:
		frappe.throw(_("No user-entered justification is available for this audit event."), frappe.ValidationError)

	try:
		context_note = decrypt(event.encrypted_user_context)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CCD Portal audit justification decryption failure")
		frappe.throw(_("The user-entered justification is unavailable."), frappe.ValidationError)

	write_event(
		"Audit Justification Viewed",
		context=PortalContext(user=user, authority="System Manager", centres=(), is_break_glass=True),
		protected_value=event.name,
		metadata={"source_event": event.name},
	)
	settings = frappe.get_single("CCD Portal Settings")
	return {
		"context_note": context_note,
		"expires_in": cint(settings.reveal_ttl_seconds) or 120,
	}


def audit_denial(event_type: str, *, context=None, record_id=None, metadata=None) -> None:
	try:
		write_event(
			event_type,
			outcome="Denied",
			context=context,
			portal_record_id=record_id,
			metadata=metadata,
		)
		# Frappe rolls back a request transaction when the caller re-raises the
		# denial. Denial paths invoke this before any governed database mutation,
		# so committing here preserves the required immutable event without
		# committing client-data changes.
		frappe.db.commit()
	except Exception:
		# A denial remains denied. Logging failures are sent to the error log because
		# returning protected data is never involved in this path.
		frappe.log_error(frappe.get_traceback(), "CCD Portal denial audit failure")
