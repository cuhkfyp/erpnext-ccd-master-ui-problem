import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


MAPPING_OPTION = "ccd_portal_centre_key: CCD Portal Canonical Centre Key"


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
	ensure_registration_mapping_option()


def ensure_registration_mapping_option() -> None:
	"""Expose the hidden centre key through the existing registration mapper."""
	if not frappe.db.exists("DocType", "CCD Field Match"):
		return
	field = frappe.get_meta("CCD Field Match").get_field("sys_fieldname")
	if not field or field.fieldtype != "Select":
		return
	options = str(field.options or "").splitlines()
	if any(option.partition(":")[0].strip() == "ccd_portal_centre_key" for option in options):
		return
	options.append(MAPPING_OPTION)
	make_property_setter(
		"CCD Field Match",
		"sys_fieldname",
		"options",
		"\n".join(options),
		"Small Text",
		validate_fields_for_doctype=False,
	)
