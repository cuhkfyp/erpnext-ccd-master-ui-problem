import frappe
from frappe import _
from frappe.model.document import Document

from ccd_portal.security import REVEAL_AUTHORITIES, split_csv


class CCDPortalPolicy(Document):
	def validate(self):
		if not self.is_new():
			old_status = frappe.db.get_value(self.doctype, self.name, "status")
			if old_status in {"Active", "Retired"}:
				frappe.throw(_("Activated policy versions are immutable."), frappe.PermissionError)
		meta = frappe.get_meta("CCD Master")
		seen = set()
		strong_searchable = False
		for row in self.fields:
			if row.fieldname in seen or not meta.has_field(row.fieldname):
				frappe.throw(_("Every policy rule must name one unique CCD Master field."))
			seen.add(row.fieldname)
			if split_csv(row.reveal_authorities) - set(REVEAL_AUTHORITIES):
				frappe.throw(_("Only Operators and Data Stewards may reveal fields."))
			if row.strong_identifier and not row.searchable:
				frappe.throw(_("A strong identifier must be searchable."))
			strong_searchable = strong_searchable or bool(row.strong_identifier and row.searchable)
		if not self.fields or not strong_searchable:
			frappe.throw(_("A policy requires at least one searchable strong identifier."))
