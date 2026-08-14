import frappe
from frappe import _
from frappe.model.document import Document

from ccd_portal.security import AUTHORITIES


class CCDPortalUserProfile(Document):
	def validate(self):
		if self.authority not in AUTHORITIES:
			frappe.throw(_("A profile must have exactly one portal authority."))
