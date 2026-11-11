from __future__ import annotations

import frappe

from ccd_portal.audit import write_event
from ccd_portal.security import PortalContext


def _lifecycle_context() -> PortalContext:
	user = str(getattr(frappe.session, "user", "") or "").strip()
	if not user or user == "Guest":
		user = "Administrator"
	return PortalContext(
		user=user,
		authority="System Manager",
		centres=(),
		is_break_glass=True,
	)


def _disable_linked_profiles(
	registration_name: str,
	*,
	trigger: str,
	registration_state: str,
) -> list[str]:
	"""Fail closed only profiles linked to this exact registration document."""
	registration_name = str(registration_name or "").strip()
	if not registration_name or not frappe.db.exists("DocType", "CCD Portal Source Profile"):
		return []

	profiles = frappe.db.sql(
		"""
		SELECT name
		  FROM `tabCCD Portal Source Profile`
		 WHERE source_registration = %s AND active = 1
		 FOR UPDATE
		""",
		(registration_name,),
		pluck=True,
	)
	for profile_name in profiles:
		# Closing both gates keeps the existing index unavailable even if someone
		# later changes Active directly without selecting and refreshing a submitted
		# registration through the governed portal workflow.
		frappe.db.set_value(
			"CCD Portal Source Profile",
			profile_name,
			{"active": 0, "sync_pending": 1},
			update_modified=True,
		)
		write_event(
			"Source Profile Auto Disabled",
			context=_lifecycle_context(),
			metadata={
				"source_profile": profile_name,
				"source_registration": registration_name,
				"registration_state": registration_state,
				"trigger": trigger,
				"fail_closed": True,
			},
		)
	return [str(name) for name in profiles]


def on_ccd_registration_cancel(doc, method: str | None = None) -> list[str]:
	"""Disable the exact portal profile when its registration is cancelled."""
	return _disable_linked_profiles(
		doc.name,
		trigger=method or "on_cancel",
		registration_state="Cancelled",
	)


def on_ccd_registration_trash(doc, method: str | None = None) -> list[str]:
	"""Disable the exact portal profile before its registration is deleted."""
	return _disable_linked_profiles(
		doc.name,
		trigger=method or "on_trash",
		registration_state="Deleted",
	)


def reconcile_invalid_source_profiles() -> dict:
	"""Repair profiles left active before lifecycle hooks were installed."""
	if not frappe.db.exists("DocType", "CCD Portal Source Profile"):
		return {"disabled": []}
	if not frappe.db.exists("DocType", "CCD Registration"):
		return {"disabled": []}

	rows = frappe.db.sql(
		"""
		SELECT profile.source_registration,
		       CASE WHEN registration.name IS NULL THEN 'Missing' ELSE 'Cancelled' END AS registration_state
		  FROM `tabCCD Portal Source Profile` profile
		  LEFT JOIN `tabCCD Registration` registration
		    ON registration.name = profile.source_registration
		 WHERE profile.active = 1
		   AND (registration.name IS NULL OR registration.docstatus <> 1)
		 ORDER BY profile.name
		""",
		as_dict=True,
	)
	disabled: list[str] = []
	for row in rows:
		disabled.extend(
			_disable_linked_profiles(
				row.source_registration,
				trigger="after_migrate_reconciliation",
				registration_state=row.registration_state,
			)
		)
	return {"disabled": disabled}
