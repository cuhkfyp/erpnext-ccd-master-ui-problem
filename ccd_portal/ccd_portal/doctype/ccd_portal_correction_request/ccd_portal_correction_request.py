import frappe
from frappe import _
from frappe.model.document import Document


class CCDPortalCorrectionRequest(Document):
	def validate(self):
		if self.status not in {"Proposed", "Approved", "Rejected", "Applied", "Stale", "Needs Review"}:
			frappe.throw(_("Invalid correction state."))
		if not self.encrypted_changes or not self.changes_digest:
			frappe.throw(_("Correction values must be encrypted and digested."))

	def on_update(self):
		if not self.flags.in_insert:
			frappe.throw(_("Correction workflow changes must use the governed API."), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("Correction requests cannot be deleted."), frappe.PermissionError)
