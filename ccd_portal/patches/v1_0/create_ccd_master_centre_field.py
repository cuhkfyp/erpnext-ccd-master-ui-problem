import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	if not frappe.db.exists("DocType", "CCD Master"):
		return
	create_custom_fields(
		{
			"CCD Master": [
				{
					"fieldname": "ccd_portal_centre_key",
					"label": "CCD Portal Canonical Centre Key",
					"fieldtype": "Data",
					"insert_after": "ccd_source_key",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
					"description": "Populated by the registration field mapping; never inferred from fuzzy links.",
				}
			]
		},
		update=True,
	)
