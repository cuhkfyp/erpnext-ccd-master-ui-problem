import frappe

from ccd_portal.source_identity import configured_source_id


def _deactivate_old_source(source: str) -> None:
	if not source:
		return
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Search Token` t
		JOIN `tabCCD Portal Record` r ON r.name=t.portal_record
		   SET t.active=0
		 WHERE r.source_registration=%s
		""",
		(source,),
	)
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Record Centre` rc
		JOIN `tabCCD Portal Record` r ON r.name=rc.portal_record
		   SET rc.active=0
		 WHERE r.source_registration=%s
		""",
		(source,),
	)
	frappe.db.sql(
		"UPDATE `tabCCD Portal Record` SET active=0 WHERE source_registration=%s",
		(source,),
	)


def execute():
	if not frappe.db.exists("DocType", "CCD Portal Source Profile"):
		return
	registration_meta = frappe.get_meta("CCD Registration")
	if not registration_meta.has_field("ccd_stable_source_key"):
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
			frappe.throw(f"CCD Portal Source Profile {row.name} has no valid stable source identity")
		if source_id in owners and owners[source_id] != row.name:
			frappe.throw(f"Multiple CCD Portal Source Profiles govern stable source {source_id}")
		owners[source_id] = row.name

	for row, source_id in resolved:
		if row.canonical_source_id == source_id:
			continue
		_deactivate_old_source(str(row.canonical_source_id or ""))
		values = {"canonical_source_id": source_id}
		if row.active:
			# The corrected source remains fail-closed until a governed refresh
			# publishes tokens and centre relations under its durable identity.
			values["sync_pending"] = 1
		frappe.db.set_value(
			"CCD Portal Source Profile",
			row.name,
			values,
			update_modified=False,
		)
