import frappe


def execute():
	indexes = (
		(
			"CCD Portal Record",
			["policy_version", "active", "ccd_master_record"],
			"ccd_portal_policy_master_lookup",
		),
		(
			"CCD Portal Identity Concern",
			["reporter", "status", "creation"],
			"ccd_portal_concern_reporter",
		),
	)
	for doctype, fields, name in indexes:
		if frappe.db.exists("DocType", doctype):
			frappe.db.add_index(doctype, fields, name)
