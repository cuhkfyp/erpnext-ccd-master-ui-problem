from __future__ import annotations

import frappe


def _disable_linked_profiles(registration_name: str) -> list[str]:
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
	return [str(name) for name in profiles]


def on_ccd_registration_cancel(doc, method: str | None = None) -> list[str]:
	"""Disable the exact portal profile when its registration is cancelled."""
	return _disable_linked_profiles(doc.name)


def on_ccd_registration_trash(doc, method: str | None = None) -> list[str]:
	"""Disable the exact portal profile before its registration is deleted."""
	return _disable_linked_profiles(doc.name)


def reconcile_invalid_source_profiles() -> dict:
	"""Repair profiles left active before lifecycle hooks were installed."""
	if not frappe.db.exists("DocType", "CCD Portal Source Profile"):
		return {"disabled": []}
	if not frappe.db.exists("DocType", "CCD Registration"):
		return {"disabled": []}

	rows = frappe.db.sql(
		"""
		SELECT profile.source_registration
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
		disabled.extend(_disable_linked_profiles(row.source_registration))
	return {"disabled": disabled}
