import re

import frappe
from frappe import _
from frappe.model.document import Document


class CCDPortalSourceProfile(Document):
	def validate(self):
		if self.parser_type == "Exact":
			self.delimiter = ""
			self.parser_pattern = ""
		elif self.parser_type == "Delimited":
			self.delimiter = (self.delimiter or ",").strip() or ","
			self.parser_pattern = ""
		elif self.parser_type == "Regular Expression":
			self.delimiter = ""
			if not self.parser_pattern or len(self.parser_pattern) > 300:
				frappe.throw(_("A bounded parser pattern is required."))
			try:
				re.compile(self.parser_pattern)
			except re.error:
				frappe.throw(_("The parser pattern is invalid."))
		else:
			frappe.throw(_("Select a supported centre-key parser."))
