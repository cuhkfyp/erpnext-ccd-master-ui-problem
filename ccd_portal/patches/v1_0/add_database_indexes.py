import frappe


def execute():
	indexes = [
		("CCD Portal Record", ["portal_record_id", "active"], "ccd_portal_record_lookup"),
		("CCD Portal Record", ["ccd_master_record", "active"], "ccd_portal_master_link"),
		("CCD Portal Record", ["source_registration", "active"], "ccd_portal_source_lookup"),
		(
			"CCD Portal Search Token",
			["fieldname", "token", "policy_version", "active"],
			"ccd_portal_token_policy_lookup",
		),
		("CCD Portal Record Centre", ["portal_record", "centre", "active"], "ccd_portal_scope_lookup"),
		("CCD Portal Centre Grant", ["user", "active", "centre"], "ccd_portal_grant_lookup"),
		("CCD Portal Audit Event", ["actor", "event_type", "creation"], "ccd_portal_audit_lookup"),
		(
			"CCD Portal Correction Request",
			["centre", "status", "creation"],
			"ccd_portal_correction_queue",
		),
	]
	for doctype, fields, name in indexes:
		if frappe.db.exists("DocType", doctype):
			frappe.db.add_index(doctype, fields, name)
