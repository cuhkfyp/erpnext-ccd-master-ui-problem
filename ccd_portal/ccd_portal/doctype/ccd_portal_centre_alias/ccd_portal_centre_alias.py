from frappe.model.document import Document


class CCDPortalCentreAlias(Document):
	def validate(self):
		self.alias_code = (self.alias_code or "").strip()
