from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from ccd_portal.policy import get_active_policy
from ccd_portal.security import normalize_value, opaque_record_id, search_token


def _source_profile(source: str) -> dict | None:
	row = frappe.db.get_value(
		"CCD Portal Source Profile",
		{"source_registration": source, "active": 1},
		["name", "assignment_mode", "parser_type", "delimiter", "parser_pattern"],
		as_dict=True,
	)
	if not row:
		return None
	profile = dict(row)
	profile["assignment_mode"] = profile.get("assignment_mode") or "Per-record Centre Key"
	profile["fixed_centres"] = frappe.get_all(
		"CCD Portal Source Fixed Centre",
		filters={"parent": row.name, "parenttype": "CCD Portal Source Profile", "parentfield": "fixed_centres"},
		pluck="centre",
		order_by="idx asc",
	)
	return profile


def _parse_centre_keys(raw_value: str, profile: dict) -> list[str]:
	raw = str(raw_value or "").strip()
	if not raw:
		return []
	parser_type = profile.get("parser_type") or "Exact"
	if parser_type == "Delimited":
		delimiter = profile.get("delimiter") or ","
		return sorted({part.strip() for part in raw.split(delimiter) if part.strip()})
	if parser_type == "Regular Expression":
		pattern = profile.get("parser_pattern") or ""
		if not pattern or len(pattern) > 300:
			return []
		try:
			matches = re.findall(pattern, raw)
		except re.error:
			return []
		return sorted(
			{
				(item[0] if isinstance(item, tuple) else item).strip()
				for item in matches
				if (item[0] if isinstance(item, tuple) else item).strip()
			}
		)
	return [raw]


def _resolve_centre(key: str, profile: dict) -> str | None:
	centre = frappe.db.get_value("CCD Portal Centre", {"name": key, "active": 1}, "name")
	if centre:
		return centre
	filters = {"alias_code": key, "active": 1}
	aliases = frappe.get_all(
		"CCD Portal Centre Alias",
		filters=filters,
		fields=["centre", "source_profile"],
		limit=3,
	)
	matches = {
		row.centre
		for row in aliases
		if not row.source_profile or row.source_profile == profile.get("name")
	}
	return next(iter(matches)) if len(matches) == 1 else None


def _get_or_create_portal_record(doc) -> object:
	opaque_id = opaque_record_id(doc.ccd_reg_source, doc.ccd_source_key)
	name = frappe.db.get_value("CCD Portal Record", {"portal_record_id": opaque_id}, "name")
	# A CCD row whose governed source identity changes must not retain the old
	# row's tokens or centre relations. Fuzzy/person links are intentionally not
	# consulted here.
	for stale_name in frappe.get_all(
		"CCD Portal Record",
		filters={"ccd_master_record": doc.name, "active": 1},
		pluck="name",
	):
		if stale_name != name:
			_deactivate_portal_record(stale_name)
	values = {
		"portal_record_id": opaque_id,
		"ccd_master_record": doc.name,
		"source_registration": doc.ccd_reg_source,
		"source_key_digest": opaque_record_id(doc.ccd_reg_source, doc.ccd_source_key)[5:],
		"source_modified": doc.modified,
		"active": 1,
		"last_indexed_on": now_datetime(),
	}
	if name:
		record = frappe.get_doc("CCD Portal Record", name)
		record.update(values)
		record.save(ignore_permissions=True)
		return record
	values.update({"doctype": "CCD Portal Record"})
	record = frappe.get_doc(values)
	record.insert(ignore_permissions=True)
	return record


def _deactivate_portal_record(name: str) -> None:
	frappe.db.set_value("CCD Portal Record", name, "active", 0, update_modified=False)
	frappe.db.set_value("CCD Portal Search Token", {"portal_record": name}, "active", 0, update_modified=False)
	frappe.db.set_value("CCD Portal Record Centre", {"portal_record": name}, "active", 0, update_modified=False)


def index_record(ccd_master_name: str) -> dict:
	meta = frappe.get_meta("CCD Master")
	doc = frappe.get_doc("CCD Master", ccd_master_name)
	if not doc.ccd_reg_source or not doc.ccd_source_key:
		return {"status": "unmapped", "reason": "missing_source_identity"}
	identity_names = frappe.get_all(
		"CCD Master",
		filters={"ccd_reg_source": doc.ccd_reg_source, "ccd_source_key": doc.ccd_source_key},
		pluck="name",
		limit=2,
	)
	if len(identity_names) != 1:
		portal_name = frappe.db.get_value(
			"CCD Portal Record",
			{"portal_record_id": opaque_record_id(doc.ccd_reg_source, doc.ccd_source_key)},
			"name",
		)
		if portal_name:
			_deactivate_portal_record(portal_name)
		return {"status": "unmapped", "reason": "duplicate_source_identity"}
	policy, field_policies = get_active_policy()

	record = _get_or_create_portal_record(doc)
	frappe.db.delete("CCD Portal Search Token", {"portal_record": record.name})
	frappe.db.delete("CCD Portal Record Centre", {"portal_record": record.name})

	profile = _source_profile(doc.ccd_reg_source)
	if not profile:
		return {"status": "unmapped", "record": record.name, "reason": "source_profile"}

	if profile["assignment_mode"] == "Fixed Centres":
		configured = sorted(set(profile["fixed_centres"]))
		centres = sorted(
			frappe.get_all(
				"CCD Portal Centre",
				filters={"name": ["in", configured], "active": 1},
				pluck="name",
			)
		) if configured else []
		if not centres or centres != configured:
			return {"status": "unmapped", "record": record.name, "reason": "fixed_centres"}
		provenance = f"Fixed centres from source profile {profile['name']}"
	else:
		if not meta.has_field("ccd_portal_centre_key"):
			frappe.throw(_("CCD Master centre-key field is not installed."))
		parsed = _parse_centre_keys(doc.get("ccd_portal_centre_key"), profile)
		centres = sorted({centre for key in parsed if (centre := _resolve_centre(key, profile))})
		if not centres or len(centres) != len(set(parsed)):
			return {"status": "unmapped", "record": record.name, "reason": "centre_alias"}
		provenance = f"Centre key parsed by source profile {profile['name']}"

	for centre in centres:
		frappe.get_doc(
			{
				"doctype": "CCD Portal Record Centre",
				"portal_record": record.name,
				"centre": centre,
				"provenance": provenance,
				"active": 1,
			}
		).insert(ignore_permissions=True)

	indexed = 0
	for field_policy in field_policies:
		if not field_policy["searchable"] or not meta.has_field(field_policy["fieldname"]):
			continue
		normalized = normalize_value(doc.get(field_policy["fieldname"]), field_policy["data_kind"])
		if not normalized:
			continue
		frappe.get_doc(
			{
				"doctype": "CCD Portal Search Token",
				"portal_record": record.name,
				"fieldname": field_policy["fieldname"],
				"token": search_token(field_policy["fieldname"], normalized),
				"policy_version": policy["policy_version"],
				"active": 1,
			}
		).insert(ignore_permissions=True)
		indexed += 1

	return {"status": "indexed", "record": record.name, "centres": len(centres), "tokens": indexed}


def mark_record_deleted(doc) -> None:
	if not doc.ccd_reg_source or not doc.ccd_source_key:
		return
	opaque_id = opaque_record_id(doc.ccd_reg_source, doc.ccd_source_key)
	name = frappe.db.get_value("CCD Portal Record", {"portal_record_id": opaque_id}, "name")
	if not name:
		return
	_deactivate_portal_record(name)


def mark_source_identity_deleted(source: str, source_key: str) -> bool:
	"""Deactivate an index entry when an authorized raw-SQL sync deleted its source row."""
	if not source or not source_key:
		return False
	name = frappe.db.get_value(
		"CCD Portal Record",
		{"portal_record_id": opaque_record_id(source, source_key), "active": 1},
		"name",
	)
	if not name:
		return False
	_deactivate_portal_record(name)
	return True


def deactivate_missing_records(source: str | None = None) -> int:
	"""Clean stale portal rows left by raw SQL deletes without loading client values."""
	values: list[str] = []
	source_clause = ""
	if source:
		source_clause = " AND r.source_registration = %s"
		values.append(source)
	rows = frappe.db.sql(
		f"""
		SELECT r.name
		  FROM `tabCCD Portal Record` r
		  LEFT JOIN `tabCCD Master` m ON m.name = r.ccd_master_record
		 WHERE r.active = 1 AND m.name IS NULL{source_clause}
		""",
		values,
		pluck=True,
	)
	for name in rows:
		_deactivate_portal_record(name)
	return len(rows)


def refresh_source(
	source: str | None = None,
	source_keys: list[str] | None = None,
	*,
	strict: bool = False,
) -> dict:
	filters: dict = {}
	if source:
		filters["ccd_reg_source"] = source
	if source_keys:
		filters["ccd_source_key"] = ["in", source_keys]
	names = frappe.get_all("CCD Master", filters=filters, pluck="name", order_by="name")
	counts = {"indexed": 0, "unmapped": 0, "failed": 0}
	seen_records: set[str] = set()
	for position, name in enumerate(names):
		save_point = f"ccd_portal_index_{position}"
		frappe.db.savepoint(save_point)
		try:
			result = index_record(name)
			counts[result["status"]] += 1
			if result.get("record"):
				seen_records.add(result["record"])
		except Exception:
			frappe.db.rollback(save_point=save_point)
			if strict:
				raise
			counts["failed"] += 1
			frappe.log_error(frappe.get_traceback(), "CCD Portal index refresh failure")
		else:
			frappe.db.release_savepoint(save_point)

	deactivated = 0
	if source and not source_keys:
		# Full-sync cleanup: source identity is represented by a digest only, so stale
		# detection compares current CCD document links with portal records.
		current_names = set(names)
		for row in frappe.get_all(
			"CCD Portal Record",
			filters={"active": 1, "source_registration": source},
			fields=["name", "ccd_master_record"],
		):
			if row.name not in seen_records and row.ccd_master_record not in current_names:
				_deactivate_portal_record(row.name)
				deactivated += 1

	return {"total": len(names), **counts, "deactivated": deactivated}


def coverage_report() -> dict:
	total = frappe.db.count("CCD Master")
	indexed = frappe.db.sql(
		"""
		SELECT COUNT(DISTINCT r.name)
		  FROM `tabCCD Portal Record` r
		  JOIN `tabCCD Portal Record Centre` rc ON rc.portal_record = r.name AND rc.active = 1
		  JOIN `tabCCD Portal Centre` c ON c.name = rc.centre AND c.active = 1
		 WHERE r.active = 1
		   AND (rc.effective_from IS NULL OR rc.effective_from <= CURRENT_DATE)
		   AND (rc.effective_to IS NULL OR rc.effective_to >= CURRENT_DATE)
		"""
	)[0][0]
	by_source = frappe.db.sql(
		"""
		SELECT m.ccd_reg_source AS source, COUNT(*) AS source_records,
		       COUNT(DISTINCT CASE WHEN r.active = 1 AND rc.active = 1 AND c.active = 1
		         AND (rc.effective_from IS NULL OR rc.effective_from <= CURRENT_DATE)
		         AND (rc.effective_to IS NULL OR rc.effective_to >= CURRENT_DATE)
		         THEN m.name END) AS mapped_records
		  FROM `tabCCD Master` m
		  LEFT JOIN `tabCCD Portal Record` r ON r.ccd_master_record = m.name
		  LEFT JOIN `tabCCD Portal Record Centre` rc ON rc.portal_record = r.name
		  LEFT JOIN `tabCCD Portal Centre` c ON c.name = rc.centre
		 GROUP BY m.ccd_reg_source ORDER BY m.ccd_reg_source
		""",
		as_dict=True,
	)
	return {
		"total_records": total,
		"mapped_records": indexed,
		"unmapped_records": max(total - indexed, 0),
		"coverage_percent": round((indexed / total * 100) if total else 100, 2),
		"by_source": [dict(row) for row in by_source],
	}
