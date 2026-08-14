import frappe
from frappe import _
from frappe.model.document import Document


class CCDPortalAuditEvent(Document):
	def before_insert(self):
		if not self.actor:
			self.actor = getattr(frappe.session, "user", "Guest")

	def on_update(self):
		if not self.flags.in_insert:
			frappe.throw(_("CCD Portal audit events are immutable."), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("CCD Portal audit events cannot be deleted."), frappe.PermissionError)
