import frappe


INDEX_NAME = "ccd_portal_token_record"


def execute():
	"""Make record-specific token replacement a targeted lookup.

	Source refreshes replace the tokens for each portal record.  Without this
	leading index, every replacement scans the complete token table.
	"""
	if frappe.db.exists("DocType", "CCD Portal Search Token"):
		frappe.db.add_index(
			"CCD Portal Search Token",
			["portal_record"],
			INDEX_NAME,
		)
