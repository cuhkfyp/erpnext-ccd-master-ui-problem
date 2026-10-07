import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class CCDPortalSettings(Document):
	def validate(self):
		if self.flags.get("same_person_governance"):
			return
		old_enabled = cint(frappe.db.get_single_value(self.doctype, "same_person_enabled"))
		new_enabled = cint(self.same_person_enabled)
		old_pilots = set(
			frappe.get_all(
				"CCD Portal Same Person Pilot User",
				filters={
					"parent": self.name,
					"parenttype": self.doctype,
					"parentfield": "same_person_pilot_users",
				},
				pluck="user",
			)
		)
		new_pilots = {row.user for row in (self.same_person_pilot_users or []) if row.user}
		if old_enabled != new_enabled or old_pilots != new_pilots:
			frappe.throw(
				_("Use the governed CCD Portal rollout actions to change same-person settings."),
				frappe.PermissionError,
			)
