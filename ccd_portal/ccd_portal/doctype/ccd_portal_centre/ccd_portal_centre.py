import frappe
from frappe import _
from frappe.model.document import Document
import re


class CCDPortalCentre(Document):
	def validate(self):
		self.centre_code = (self.centre_code or "").strip()
		if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,139}", self.centre_code or ""):
			frappe.throw(_("Enter a valid canonical centre code."))
