import frappe
from frappe.installer import update_site_config
from frappe.utils import cint

from ccd_portal.primitives import SERVICE_FALLBACK_UNSPECIFIED, SERVICE_NAME_FALLBACKS


REQUIRED_SETTING_DEFAULTS = {
	"searches_per_hour": 60,
	"reveals_per_hour": 20,
	"concerns_per_hour": 10,
	"reveal_ttl_seconds": 120,
}


def after_install() -> None:
	ensure_external_schema()
	settings = frappe.get_single("CCD Portal Settings")
	settings.enabled = 0
	settings.administrator_preview = 1
	settings.searches_per_hour = 60
	settings.reveals_per_hour = 20
	settings.concerns_per_hour = 10
	settings.reveal_ttl_seconds = 120
	settings.same_person_enabled = 0
	settings.service_name_fallback = SERVICE_FALLBACK_UNSPECIFIED
	settings.flags.same_person_governance = True
	settings.save(ignore_permissions=True)
	ensure_initial_draft_policy()
	ensure_development_administrator()


def after_migrate() -> None:
	ensure_external_schema()
	ensure_settings_defaults()


def ensure_settings_defaults() -> dict:
	"""Backfill newly required singleton values without replacing configured limits."""
	updated = []
	for fieldname, default in REQUIRED_SETTING_DEFAULTS.items():
		value = frappe.db.get_single_value("CCD Portal Settings", fieldname)
		if cint(value) <= 0:
			frappe.db.set_single_value("CCD Portal Settings", fieldname, default)
			updated.append(fieldname)
	service_fallback = frappe.db.get_single_value("CCD Portal Settings", "service_name_fallback")
	if service_fallback not in SERVICE_NAME_FALLBACKS:
		frappe.db.set_single_value(
			"CCD Portal Settings", "service_name_fallback", SERVICE_FALLBACK_UNSPECIFIED
		)
		updated.append("service_name_fallback")
	return {"updated": updated}


def ensure_external_schema() -> None:
	"""Idempotently maintain the one governed field and portal query indexes."""
	from ccd_portal.patches.v1_0.add_database_indexes import execute as add_database_indexes
	from ccd_portal.patches.v1_0.create_ccd_master_centre_field import execute as create_centre_field
	from ccd_portal.patches.v2_0.add_same_person_indexes import execute as add_same_person_indexes
	from ccd_portal.patches.v2_0.add_search_token_record_index import (
		execute as add_search_token_record_index,
	)
	from ccd_portal.patches.v2_0.add_source_refresh_indexes import execute as add_source_refresh_indexes

	create_centre_field()
	add_database_indexes()
	add_same_person_indexes()
	add_search_token_record_index()
	add_source_refresh_indexes()


def ensure_site_secret() -> dict:
	"""Create the site-local HMAC key once without printing or committing it."""
	if frappe.conf.get("ccd_portal_hmac_secret"):
		return {"created": False}
	secret = frappe.generate_hash(length=64)
	update_site_config("ccd_portal_hmac_secret", secret, validate=False)
	frappe.local.conf["ccd_portal_hmac_secret"] = secret
	return {"created": True}


def ensure_development_administrator() -> dict:
	"""Give only the built-in Administrator governance access while the flag is off."""
	if not frappe.db.exists("User", "Administrator"):
		return {"created": False}
	name = frappe.db.exists("CCD Portal User Profile", {"user": "Administrator"})
	if name:
		return {"created": False}
	frappe.get_doc(
		{
			"doctype": "CCD Portal User Profile",
			"user": "Administrator",
			"authority": "Access Administrator",
			"active": 1,
		}
	).insert(ignore_permissions=True)
	return {"created": True}


def ensure_initial_draft_policy() -> None:
	for code, label, order in (
		("SERVICE_DELIVERY", "Deliver an authorized service", 10),
		("IDENTITY_VERIFICATION", "Verify identity for an authorized transaction", 20),
		("CORRECTION_REVIEW", "Review a submitted correction", 30),
		("SAFEGUARDING", "Respond to an approved safeguarding need", 40),
	):
		if not frappe.db.exists("CCD Portal Reveal Reason", code):
			frappe.get_doc(
				{
					"doctype": "CCD Portal Reveal Reason",
					"reason_code": code,
					"label": label,
					"display_order": order,
					"active": 1,
				}
			).insert(ignore_permissions=True)
	if frappe.db.exists("CCD Portal Policy", "CCD-PORTAL-V1-DRAFT"):
		return
	rules = [
		("hksr_num", "Membership Number", "Identity", "Identifier", "Last 4", 1, 1, 1),
		("hkid", "HKID", "Identity", "HKID", "Last 4", 1, 1, 1),
		("bc_num", "Birth Certificate Number", "Identity", "Birth Certificate", "Last 4", 1, 1, 1),
		("eng_firstname", "English First Name", "Identity", "Name", "First Character", 1, 0, 1),
		("eng_surname", "English Surname", "Identity", "Name", "First Character", 1, 0, 1),
		("chi_firstname", "Chinese First Name", "Identity", "Name", "First Character", 1, 0, 1),
		("chi_surname", "Chinese Surname", "Identity", "Name", "First Character", 1, 0, 1),
		("birthday", "Date of Birth", "Identity", "Date", "Year Only", 1, 0, 1),
		("res_country", "Residential Country", "Contact", "Text", "Full", 0, 0, 1),
		("res_area", "Residential Area", "Contact", "Text", "Full", 0, 0, 1),
		("res_district", "Residential District", "Contact", "Text", "Full", 0, 0, 1),
		("res_addr1", "Residential Address 1", "Contact", "Text", "Full", 0, 0, 1),
		("res_addr2", "Residential Address 2", "Contact", "Text", "Full", 0, 0, 1),
		("res_addr3", "Residential Address 3", "Contact", "Text", "Full", 0, 0, 1),
		("res_phone", "Residential Phone", "Contact", "Phone", "Phone", 1, 1, 1),
		("pos_country", "Postal Country", "Contact", "Text", "Full", 0, 0, 1),
		("post_area", "Postal Area", "Contact", "Text", "Full", 0, 0, 1),
		("post_district", "Postal District", "Contact", "Text", "Full", 0, 0, 1),
		("post_addr1", "Postal Address 1", "Contact", "Text", "Full", 0, 0, 1),
		("post_addr2", "Postal Address 2", "Contact", "Text", "Full", 0, 0, 1),
		("post_addr3", "Postal Address 3", "Contact", "Text", "Full", 0, 0, 1),
		("phone_num", "Primary Phone", "Contact", "Phone", "Phone", 1, 1, 1),
		("mobile", "Mobile Phone", "Contact", "Phone", "Phone", 1, 1, 1),
		("email", "Email", "Contact", "Email", "Email", 1, 1, 1),
		("contact1_name", "Contact 1 Name", "Contact", "Name", "Initials", 0, 0, 1),
		("contact1_phone", "Contact 1 Phone", "Contact", "Phone", "Phone", 0, 0, 1),
		("contact2_name", "Contact 2 Name", "Contact", "Name", "Initials", 0, 0, 1),
		("contact2_phone", "Contact 2 Phone", "Contact", "Phone", "Phone", 0, 0, 1),
	]
	meta = frappe.get_meta("CCD Master")
	fields = [
		{
			"source": "CCD Master",
			"fieldname": fieldname,
			"label": label,
			"classification": classification,
			"data_kind": kind,
			"mask_strategy": mask,
			"reveal_authorities": "Operator,Data Steward",
			"searchable": searchable,
			"strong_identifier": strong,
			"correctable": correctable,
			"display_order": index * 10,
		}
		for index, (fieldname, label, classification, kind, mask, searchable, strong, correctable) in enumerate(
			rules, start=1
		)
		if meta.has_field(fieldname)
	]
	frappe.get_doc(
		{
			"doctype": "CCD Portal Policy",
			"policy_version": "CCD-PORTAL-V1-DRAFT",
			"title": "Initial V1 policy — review before activation",
			"status": "Draft",
			"fields": fields,
		}
	).insert(ignore_permissions=True)


def before_tests() -> None:
	# Test data is created by each test and uses only SYNTHETIC-* identifiers.
	frappe.db.set_single_value("CCD Portal Settings", "enabled", 1)
