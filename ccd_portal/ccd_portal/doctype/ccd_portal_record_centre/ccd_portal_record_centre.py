import frappe
from frappe import _
from frappe.model.document import Document


class CCDPortalRecordCentre(Document):
	def validate(self):
		if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
			frappe.throw(_("Relation end date cannot precede its start date."))
