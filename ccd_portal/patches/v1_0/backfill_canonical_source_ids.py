import frappe

from ccd_portal.source_identity import canonical_source_id


def execute():
	if not frappe.db.exists("DocType", "CCD Portal Source Profile"):
		return
	if not frappe.get_meta("CCD Portal Source Profile").has_field("canonical_source_id"):
		return

	rows = frappe.get_all(
		"CCD Portal Source Profile",
		fields=["name", "source_registration", "canonical_source_id"],
		order_by="creation asc",
	)
	owners: dict[str, str] = {}
	for row in rows:
		source_id = canonical_source_id(row.source_registration)
		if not source_id:
			frappe.throw(f"CCD Portal Source Profile {row.name} has no valid source registration")
		if source_id in owners and owners[source_id] != row.name:
			frappe.throw(f"Multiple CCD Portal Source Profiles govern canonical source {source_id}")
		owners[source_id] = row.name
		if row.canonical_source_id != source_id:
			frappe.db.set_value(
				"CCD Portal Source Profile",
				row.name,
				"canonical_source_id",
				source_id,
				update_modified=False,
			)
