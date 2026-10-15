import frappe


def execute():
	if frappe.db.exists("DocType", "CCD Portal Source Refresh"):
		frappe.db.add_index(
			"CCD Portal Source Refresh",
			["source_registration", "status", "creation"],
			"ccd_portal_source_refresh_lookup",
		)
