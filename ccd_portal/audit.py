from __future__ import annotations

from typing import Any

import frappe

from ccd_portal.security import canonical_json, protected_digest


def write_event(
	event_type: str,
	*,
	outcome: str = "Allowed",
	context=None,
	portal_record_id: str | None = None,
	criteria_types: list[str] | None = None,
	protected_value: Any = None,
	reason_code: str | None = None,
	metadata: dict | None = None,
) -> str:
	"""Write an audit event. Callers intentionally fail if this insert fails."""
	user = context.user if context else getattr(frappe.session, "user", "Guest")
	authority = context.authority if context else "Unauthenticated"
	digest = None
	if protected_value is not None:
		digest = protected_digest(canonical_json(protected_value), "audit-event")

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
			"request_id": getattr(frappe.local, "request_id", None),
			"event_metadata": canonical_json(metadata or {}),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


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
