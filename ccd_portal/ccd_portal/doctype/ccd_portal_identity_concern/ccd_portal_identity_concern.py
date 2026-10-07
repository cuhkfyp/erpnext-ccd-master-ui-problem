import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from ccd_portal.audit import write_event
from ccd_portal.security import PortalContext, clean_reason


TRANSITIONS = {
	"Submitted": {"In Review", "Dismissed"},
	"In Review": {"Resolved", "Dismissed"},
}


class CCDPortalIdentityConcern(Document):
	def validate(self):
		if self.is_new():
			if self.status != "Submitted" or not 2 <= len(self.records or []) <= 20:
				frappe.throw(_("Invalid identity concern."), frappe.ValidationError)
			if self.reporter_authority not in {"Operator", "Data Steward"}:
				frappe.throw(_("Invalid identity concern reporter."), frappe.ValidationError)
			if self.record_count != len(self.records) or not self.person_digest or not self.encrypted_context:
				frappe.throw(_("Invalid protected concern context."), frappe.ValidationError)
			if len({row.portal_record_id for row in self.records}) != len(self.records):
				frappe.throw(_("Each concern record may be selected once."), frappe.ValidationError)
			return
		old = self.get_doc_before_save()
		if not old or self.status not in TRANSITIONS.get(old.status, set()):
			frappe.throw(_("Invalid concern status transition."), frappe.ValidationError)
		roles = set(frappe.get_roles())
		if "System Manager" not in roles and "CCD Match Sensitive Reviewer" not in roles:
			frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)
		for fieldname in (
			"reporter",
			"reporter_authority",
			"record_count",
			"person_digest",
			"encrypted_context",
			"context_digest",
		):
			if str(self.get(fieldname) or "") != str(old.get(fieldname) or ""):
				frappe.throw(_("Concern submission details are immutable."), frappe.PermissionError)
		if [row.portal_record_id for row in self.records] != [row.portal_record_id for row in old.records]:
			frappe.throw(_("Concern record references are immutable."), frappe.PermissionError)
		self.review_reason = clean_reason(self.review_reason)
		self.reviewed_by = frappe.session.user
		self.reviewed_on = now_datetime()

	def on_update(self):
		if self.flags.in_insert:
			return
		roles = set(frappe.get_roles())
		authority = "System Manager" if "System Manager" in roles else "CCD Match Sensitive Reviewer"
		write_event(
			"Identity Concern Status Changed",
			context=PortalContext(frappe.session.user, authority, ()),
			protected_value=self.review_reason,
			metadata={"concern": self.name, "status": self.status},
		)

	def on_trash(self):
		frappe.throw(_("Identity concerns cannot be deleted."), frappe.PermissionError)
