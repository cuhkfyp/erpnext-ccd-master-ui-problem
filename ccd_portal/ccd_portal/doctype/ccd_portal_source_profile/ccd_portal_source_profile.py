import re

import frappe
from frappe import _
from frappe.model.document import Document


class CCDPortalSourceProfile(Document):
	def validate(self):
		self.assignment_mode = self.assignment_mode or "Per-record Centre Key"
		if self.assignment_mode == "Fixed Centres":
			centres = [str(row.centre or "").strip() for row in self.fixed_centres]
			if not centres or any(not centre for centre in centres):
				frappe.throw(_("Select at least one fixed centre."))
			if len(centres) > 20 or len(set(centres)) != len(centres):
				frappe.throw(_("Fixed centres must be unique and no more than 20 may be selected."))
			active_centres = set(
				frappe.get_all(
					"CCD Portal Centre",
					filters={"name": ["in", centres], "active": 1},
					pluck="name",
				)
			)
			if active_centres != set(centres):
				frappe.throw(_("Every fixed centre must exist and be active."))
			self.parser_type = "Exact"
			self.delimiter = ""
			self.parser_pattern = ""
			return

		if self.assignment_mode != "Per-record Centre Key":
			frappe.throw(_("Select a supported centre assignment mode."))
		self.set("fixed_centres", [])
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
