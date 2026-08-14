from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import cint, nowdate

from ccd_portal.primitives import canonical_json, is_valid_hkid, mask_value, normalize_value

AUTHORITIES = ("Reader", "Operator", "Data Steward", "Access Administrator")
CLIENT_AUTHORITIES = ("Reader", "Operator", "Data Steward")
REVEAL_AUTHORITIES = ("Operator", "Data Steward")
GENERIC_DENIAL = _("You are not permitted to perform this action.")


@dataclass(frozen=True)
class PortalContext:
	user: str
	authority: str
	centres: tuple[str, ...]
	is_break_glass: bool = False


def _settings():
	return frappe.get_single("CCD Portal Settings")


def has_portal_permission() -> bool:
	try:
		require_context(allow_access_admin=True)
		return True
	except Exception:
		return False


def require_post() -> None:
	request = getattr(frappe.local, "request", None)
	if request and request.method != "POST":
		frappe.throw(_("POST is required."), frappe.PermissionError)


def require_context(
	*allowed: str,
	allow_access_admin: bool = False,
	require_centres: bool = False,
) -> PortalContext:
	user = getattr(frappe.session, "user", "Guest")
	if user == "Guest":
		frappe.throw(GENERIC_DENIAL, frappe.AuthenticationError)

	settings = _settings()
	preview_users = {row.user for row in (settings.preview_users or []) if row.user}
	if not cint(settings.enabled) and user != "Administrator" and user not in preview_users:
		frappe.throw(_("CCD Portal is not enabled for this account."), frappe.PermissionError)

	profile = frappe.db.get_value(
		"CCD Portal User Profile",
		{"user": user, "active": 1},
		["name", "authority"],
		as_dict=True,
	)
	if not profile:
		frappe.throw(GENERIC_DENIAL, frappe.PermissionError)
	if profile.authority not in AUTHORITIES:
		frappe.throw(GENERIC_DENIAL, frappe.PermissionError)
	if profile.authority == "Access Administrator" and not allow_access_admin:
		frappe.throw(GENERIC_DENIAL, frappe.PermissionError)
	if allowed and profile.authority not in allowed:
		frappe.throw(GENERIC_DENIAL, frappe.PermissionError)

	centres = tuple(get_effective_centres(user))
	if require_centres and not centres:
		frappe.throw(GENERIC_DENIAL, frappe.PermissionError)
	return PortalContext(user=user, authority=profile.authority, centres=centres)


def require_access_administrator() -> PortalContext:
	return require_context("Access Administrator", allow_access_admin=True)


def require_system_manager(reason: str) -> PortalContext:
	user = getattr(frappe.session, "user", "Guest")
	if user == "Guest" or "System Manager" not in frappe.get_roles(user):
		frappe.throw(GENERIC_DENIAL, frappe.PermissionError)
	reason = clean_reason(reason)
	return PortalContext(user=user, authority="System Manager", centres=(), is_break_glass=True)


def get_effective_centres(user: str) -> list[str]:
	today = nowdate()
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT g.centre
		  FROM `tabCCD Portal Centre Grant` g
		  JOIN `tabCCD Portal Centre` c ON c.name = g.centre
		 WHERE g.user = %(user)s AND g.active = 1 AND c.active = 1
		   AND (g.effective_from IS NULL OR g.effective_from <= %(today)s)
		   AND (g.effective_to IS NULL OR g.effective_to >= %(today)s)
		 ORDER BY g.centre
		""",
		{"user": user, "today": today},
		as_dict=True,
	)
	return [row.centre for row in rows]


def ensure_record_access(portal_record_id: str, context: PortalContext) -> dict:
	if not portal_record_id or len(portal_record_id) > 80:
		frappe.throw(GENERIC_DENIAL, frappe.PermissionError)

	params = {"record_id": portal_record_id, "today": nowdate()}
	centre_clause = ""
	if not context.is_break_glass:
		if not context.centres:
			frappe.throw(GENERIC_DENIAL, frappe.PermissionError)
		placeholders = ", ".join(["%s"] * len(context.centres))
		centre_clause = f" AND rc.centre IN ({placeholders})"

	query = f"""
		SELECT r.name, r.portal_record_id, r.ccd_master_record, r.source_modified,
		       GROUP_CONCAT(DISTINCT rc.centre ORDER BY rc.centre) AS centres
		  FROM `tabCCD Portal Record` r
		  JOIN `tabCCD Portal Record Centre` rc ON rc.portal_record = r.name
		 WHERE r.portal_record_id = %(record_id)s AND r.active = 1 AND rc.active = 1
		   AND (rc.effective_from IS NULL OR rc.effective_from <= %(today)s)
		   AND (rc.effective_to IS NULL OR rc.effective_to >= %(today)s)
		   {centre_clause}
		 GROUP BY r.name, r.portal_record_id, r.ccd_master_record, r.source_modified
		 LIMIT 1
	"""
	values: dict | list
	if context.is_break_glass:
		values = params
	else:
		values = [portal_record_id, nowdate(), nowdate(), *context.centres]
		query = query.replace("%(record_id)s", "%s").replace("%(today)s", "%s", 1).replace(
			"%(today)s", "%s", 1
		)
	rows = frappe.db.sql(query, values, as_dict=True)
	if not rows:
		frappe.throw(GENERIC_DENIAL, frappe.PermissionError)
	return dict(rows[0])


def get_hmac_secret() -> bytes:
	secret = frappe.conf.get("ccd_portal_hmac_secret")
	if not secret or len(str(secret)) < 32:
		frappe.throw(
			_("CCD Portal search secret is not configured."),
			frappe.ValidationError,
		)
	return str(secret).encode("utf-8")


def protected_digest(value: str | bytes, purpose: str = "audit") -> str:
	if isinstance(value, str):
		value = value.encode("utf-8")
	return hmac.new(get_hmac_secret(), purpose.encode() + b"\x00" + value, hashlib.sha256).hexdigest()


def opaque_record_id(source: str, source_key: str) -> str:
	return "ccdp_" + protected_digest(f"{source}\x1f{source_key}", "record")[:40]


def search_token(fieldname: str, normalized: str) -> str:
	return protected_digest(f"{fieldname}\x1f{normalized}", "search")


def validate_search_value(kind: str, value: str) -> str:
	try:
		normalized = normalize_value(value, kind)
	except (TypeError, ValueError):
		frappe.throw(_("Invalid search criteria."), frappe.ValidationError)
	if not normalized or len(normalized) > 254:
		frappe.throw(_("Invalid search criteria."), frappe.ValidationError)
	if kind == "HKID" and not is_valid_hkid(normalized):
		frappe.throw(_("Enter a complete, valid HKID."), frappe.ValidationError)
	if kind == "Email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
		frappe.throw(_("Enter a valid email address."), frappe.ValidationError)
	if kind == "Phone" and not 8 <= len(normalized) <= 20:
		frappe.throw(_("Enter a valid phone number."), frappe.ValidationError)
	return normalized


def clean_reason(reason: str, *, minimum: int = 3, maximum: int = 500) -> str:
	reason = " ".join(str(reason or "").split())
	if len(reason) < minimum or len(reason) > maximum:
		frappe.throw(_("A valid reason is required."), frappe.ValidationError)
	return reason


def clean_context(value: str | None) -> str:
	return " ".join(str(value or "").split())[:500]


def split_csv(value: str | Iterable[str] | None) -> set[str]:
	if not value:
		return set()
	if isinstance(value, str):
		value = value.split(",")
	return {str(item).strip() for item in value if str(item).strip()}
