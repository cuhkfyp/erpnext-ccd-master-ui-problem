import frappe


def execute() -> None:
	"""Preserve the existing visible centre-label behaviour after this field is added."""
	frappe.db.set_single_value("CCD Portal Settings", "centre_labels_enabled", 1)
