import frappe


INDEX_NAME = "ccd_portal_token_search_cover"


def execute():
	"""Keep exact token intersections in the index instead of fetching token rows."""
	if not frappe.db.exists("DocType", "CCD Portal Search Token"):
		return
	if frappe.db.sql(
		"SHOW INDEX FROM `tabCCD Portal Search Token` WHERE Key_name = %s",
		(INDEX_NAME,),
	):
		return
	# Refuse a table-copy/blocking fallback and bound metadata-lock acquisition.
	# Existing record/token indexes remain available throughout the online build.
	previous_timeout = frappe.db.sql("SELECT @@SESSION.lock_wait_timeout")[0][0]
	frappe.db.sql("SET SESSION lock_wait_timeout = 5")
	try:
		frappe.db.sql_ddl(
			"""ALTER TABLE `tabCCD Portal Search Token`
			ADD INDEX IF NOT EXISTS `ccd_portal_token_search_cover`
			(`fieldname`, `token`, `policy_version`, `active`, `portal_record`),
			ALGORITHM=INPLACE, LOCK=NONE"""
		)
	finally:
		frappe.db.sql("SET SESSION lock_wait_timeout = %s", (previous_timeout,))
