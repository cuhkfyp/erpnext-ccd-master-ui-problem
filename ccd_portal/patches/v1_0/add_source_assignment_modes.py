import frappe


def execute():
	if not frappe.db.exists("DocType", "CCD Portal Source Profile"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabCCD Portal Source Profile`
		   SET assignment_mode = 'Per-record Centre Key'
		 WHERE assignment_mode IS NULL OR assignment_mode = ''
		"""
	)
