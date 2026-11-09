import frappe

from ccd_portal.patches.v2_0.prefer_registration_stable_source_keys import (
	_deactivate_old_source,
)
from ccd_portal.source_identity import configured_source_id


def execute():
	"""Align profiles whose records predate their explicit connector source key."""
	if not frappe.db.exists("DocType", "CCD Portal Source Profile"):
		return

	rows = frappe.get_all(
		"CCD Portal Source Profile",
		fields=["name", "source_registration", "canonical_source_id", "active"],
		order_by="creation asc",
	)
	resolved = [(row, configured_source_id(row.source_registration)) for row in rows]
	owners: dict[str, str] = {}
	for row, source_id in resolved:
		if not source_id:
			frappe.throw(f"CCD Portal Source Profile {row.name} has no valid record source")
		if source_id in owners and owners[source_id] != row.name:
			frappe.throw(f"Multiple CCD Portal Source Profiles govern record source {source_id}")
		owners[source_id] = row.name

	for row, source_id in resolved:
		if row.canonical_source_id == source_id:
			continue
		_deactivate_old_source(str(row.canonical_source_id or ""))
		values = {"canonical_source_id": source_id}
		if row.active:
			# Changing the record source must never grant access before a complete
			# governed refresh publishes its tokens and centre relations.
			values["sync_pending"] = 1
		frappe.db.set_value(
			"CCD Portal Source Profile",
			row.name,
			values,
			update_modified=False,
		)
