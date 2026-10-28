import frappe


def execute():
	"""Preserve the pre-toggle behavior once; later administrator choices remain durable."""
	frappe.db.set_single_value("CCD Portal Settings", "identity_concerns_enabled", 1)
