import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

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
		unified_fields = []
		for row in self.fields:
			source = row.source or "CCD Master"
			if row.fieldname in seen:
				frappe.throw(_("Every policy rule must name one unique CCD Master field."))
			seen.add(row.fieldname)
			if source == "Unified Person":
				unified_fields.append(row)
			elif source != "CCD Master" or not meta.has_field(row.fieldname):
				frappe.throw(_("Every policy rule must name one governed source field."))
			if split_csv(row.reveal_authorities) - set(REVEAL_AUTHORITIES):
				frappe.throw(_("Only Operators and Data Stewards may reveal fields."))
			if row.strong_identifier and not row.searchable:
				frappe.throw(_("A strong identifier must be searchable."))
			strong_searchable = strong_searchable or bool(row.strong_identifier and row.searchable)
		if not self.fields or not strong_searchable:
			frappe.throw(_("A policy requires at least one searchable strong identifier."))
		if self.same_person_enabled:
			if len(unified_fields) != 1:
				frappe.throw(_("A V2 policy requires exactly one governed Unified Person field."))
			field = unified_fields[0]
			expected = {
				"fieldname": "unified_person_number",
				"classification": "Identity",
				"data_kind": "Identifier",
				"mask_strategy": "Last 4",
				"reveal_authorities": "Operator,Data Steward",
				"searchable": 1,
				"strong_identifier": 1,
				"correctable": 0,
			}
			if any(
				(cint(field.get(key)) != cint(value))
				if key in {"searchable", "strong_identifier", "correctable"}
				else (str(field.get(key) or "") != str(value))
				for key, value in expected.items()
			):
				frappe.throw(_("The governed Unified Person field cannot be changed."))
		elif unified_fields:
			frappe.throw(_("Unified Person fields require the V2 same-person capability."))
