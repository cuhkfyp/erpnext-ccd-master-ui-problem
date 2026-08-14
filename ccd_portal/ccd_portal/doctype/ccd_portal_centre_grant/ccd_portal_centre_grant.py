import frappe
from frappe import _
from frappe.model.document import Document


class CCDPortalCentreGrant(Document):
	def validate(self):
		if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
			frappe.throw(_("Grant end date cannot precede its start date."))
		if frappe.db.exists(
			"CCD Portal Centre Grant",
			{"user": self.user, "centre": self.centre, "name": ["!=", self.name or ""]},
		):
			frappe.throw(_("This user already has an explicit grant for the centre."))
