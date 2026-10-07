"""Read-only bridge to the governed db_connector identity registry.

The portal deliberately does not copy identity assignments. Every expansion
re-reads the current registry and fails closed for the affected person while
ordinary portal token search remains available.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint, now_datetime

from ccd_portal.identity_rules import assignment_is_eligible
from ccd_portal.primitives import is_valid_unified_person_number
from ccd_portal.security import PortalContext, protected_digest


UNIFIED_PERSON_DOCTYPE = "CCD Unified Person"
UNIFIED_MEMBERSHIP_DOCTYPE = "CCD Unified Person Membership"
UNIFIED_ALIAS_DOCTYPE = "CCD Unified Person Alias"
IDENTITY_GROUP_DOCTYPE = "CCD Identity Group"
IDENTITY_MEMBERSHIP_DOCTYPE = "CCD Identity Membership"

REQUIRED_COLUMNS = {
	"tabCCD Unified Person": {
		"name",
		"unified_person_number",
		"status",
		"canonical_person",
		"current_identity_group",
	},
	"tabCCD Unified Person Membership": {"name", "unified_person", "ccd_master", "status"},
	"tabCCD Unified Person Alias": {"alias_person", "canonical_person", "status"},
	"tabCCD Identity Group": {"name", "status"},
	"tabCCD Identity Membership": {"identity_group", "ccd_master", "status"},
	"tabCCD Portal Record": {"portal_record_id", "ccd_master_record", "policy_version", "active"},
}

REQUIRED_INDEXES = {
	"tabCCD Unified Person Membership": {"ccd_unified_member_current", "ccd_unified_person_current"},
	"tabCCD Unified Person Alias": {"ccd_unified_alias_current"},
	"tabCCD Identity Membership": {"ccd_identity_member_current", "ccd_identity_group_current"},
	"tabCCD Portal Record": {"ccd_portal_policy_master_lookup"},
}


def schema_status() -> dict[str, Any]:
	"""Return aggregate schema/index readiness without raising during search."""
	try:
		column_placeholders = ", ".join(["%s"] * len(REQUIRED_COLUMNS))
		columns = frappe.db.sql(
			f"""
			SELECT TABLE_NAME, COLUMN_NAME
			  FROM information_schema.COLUMNS
			 WHERE TABLE_SCHEMA = DATABASE()
			   AND TABLE_NAME IN ({column_placeholders})
			""",
			tuple(REQUIRED_COLUMNS),
		)
		index_placeholders = ", ".join(["%s"] * len(REQUIRED_INDEXES))
		indexes = frappe.db.sql(
			f"""
			SELECT TABLE_NAME, INDEX_NAME
			  FROM information_schema.STATISTICS
			 WHERE TABLE_SCHEMA = DATABASE()
			   AND TABLE_NAME IN ({index_placeholders})
			""",
			tuple(REQUIRED_INDEXES),
		)
	except Exception:
		return {
			"ready": False,
			"tables_ready": False,
			"indexes_ready": False,
			"missing_table_count": len(REQUIRED_COLUMNS),
			"missing_column_count": sum(len(value) for value in REQUIRED_COLUMNS.values()),
			"missing_index_count": sum(len(value) for value in REQUIRED_INDEXES.values()),
			"check_failed": True,
		}

	found_columns: dict[str, set[str]] = {table: set() for table in REQUIRED_COLUMNS}
	for table, column in columns:
		if table in found_columns:
			found_columns[table].add(column)
	found_indexes: dict[str, set[str]] = {table: set() for table in REQUIRED_INDEXES}
	for table, index in indexes:
		if table in found_indexes:
			found_indexes[table].add(index)
	missing_tables = sum(not bool(found_columns[table]) for table in REQUIRED_COLUMNS)
	missing_columns = sum(
		len(required - found_columns[table]) for table, required in REQUIRED_COLUMNS.items()
	)
	missing_indexes = sum(
		len(required - found_indexes[table]) for table, required in REQUIRED_INDEXES.items()
	)
	return {
		"ready": not (missing_tables or missing_columns or missing_indexes),
		"tables_ready": not (missing_tables or missing_columns),
		"indexes_ready": not missing_indexes,
		"missing_table_count": missing_tables,
		"missing_column_count": missing_columns,
		"missing_index_count": missing_indexes,
		"check_failed": False,
	}


def policy_supports_same_person(policy: dict) -> bool:
	return bool(cint(policy.get("same_person_enabled")))


def rollout_allows_user(settings, context: PortalContext) -> bool:
	if cint(getattr(settings, "same_person_enabled", 0)):
		return True
	pilots = {
		str(row.user)
		for row in (getattr(settings, "same_person_pilot_users", None) or [])
		if getattr(row, "user", None)
	}
	return context.user in pilots


def feature_state(policy: dict, settings, context: PortalContext | None = None) -> dict[str, Any]:
	schema = schema_status()
	policy_enabled = policy_supports_same_person(policy)
	rollout_enabled = rollout_allows_user(settings, context) if context else bool(cint(settings.same_person_enabled))
	return {
		"effective": bool(policy_enabled and rollout_enabled and schema["ready"]),
		"policy_enabled": policy_enabled,
		"rollout_enabled": rollout_enabled,
		"schema_ready": schema["ready"],
	}


def _canonical_person_row(person_number: str) -> dict | None:
	rows = frappe.db.sql(
		"""
		SELECT p.name, p.unified_person_number, p.status, p.canonical_person,
		       p.current_identity_group, g.status AS current_group_status
		  FROM `tabCCD Unified Person` p
		  LEFT JOIN `tabCCD Identity Group` g ON g.name = p.current_identity_group
		 WHERE p.unified_person_number = %s
		 LIMIT 2
		""",
		(person_number,),
		as_dict=True,
	)
	if len(rows) != 1:
		return None
	row = dict(rows[0])
	if row["status"] == "Active" and not row.get("canonical_person"):
		return row
	if row["status"] != "Alias" or not row.get("canonical_person"):
		return None
	aliases = frappe.db.sql(
		"""
		SELECT canonical_person
		  FROM `tabCCD Unified Person Alias`
		 WHERE alias_person = %s AND status = 'Active'
		 LIMIT 2
		""",
		(row["name"],),
		as_dict=True,
	)
	if len(aliases) != 1 or str(aliases[0].canonical_person) != str(row["canonical_person"]):
		return None
	canonical = frappe.db.sql(
		"""
		SELECT p.name, p.unified_person_number, p.status, p.canonical_person,
		       p.current_identity_group, g.status AS current_group_status
		  FROM `tabCCD Unified Person` p
		  LEFT JOIN `tabCCD Identity Group` g ON g.name = p.current_identity_group
		 WHERE p.name = %s AND p.status = 'Active' AND p.canonical_person IS NULL
		 LIMIT 2
		""",
		(row["canonical_person"],),
		as_dict=True,
	)
	return dict(canonical[0]) if len(canonical) == 1 else None


def _eligible_person(person: dict | None) -> dict | None:
	if not person:
		return None
	memberships = frappe.db.sql(
		"""
		SELECT m.name, m.ccd_master
		  FROM `tabCCD Unified Person Membership` m
		  JOIN `tabCCD Master` c ON c.name = m.ccd_master
		 WHERE m.unified_person = %s AND m.status = 'Active'
		 ORDER BY m.ccd_master, m.name
		""",
		(person["name"],),
		as_dict=True,
	)
	if not memberships:
		return None
	record_names = sorted({str(row.ccd_master) for row in memberships})
	placeholders = ", ".join(["%s"] * len(record_names))
	# Detect a current record assigned to another person even when that corrupt
	# membership was not included in this person's first query.
	all_unified = frappe.db.sql(
		f"""
		SELECT name, ccd_master
		  FROM `tabCCD Unified Person Membership`
		 WHERE status = 'Active' AND ccd_master IN ({placeholders})
		 ORDER BY ccd_master, name
		""",
		record_names,
		as_dict=True,
	)
	identity_rows = frappe.db.sql(
		f"""
		SELECT im.ccd_master, im.identity_group, im.status AS membership_status,
		       g.status AS group_status
		  FROM `tabCCD Identity Membership` im
		  LEFT JOIN `tabCCD Identity Group` g ON g.name = im.identity_group
		 WHERE im.ccd_master IN ({placeholders})
		   AND im.status IN ('Active', 'Needs Revalidation')
		 ORDER BY im.ccd_master, im.identity_group
		""",
		record_names,
		as_dict=True,
	)
	eligible, _ = assignment_is_eligible(
		person,
		list(all_unified),
		list(identity_rows),
		current_group_status=person.get("current_group_status"),
	)
	if not eligible:
		return None
	return {**person, "ccd_master_records": record_names}


def person_for_number(person_number: str) -> dict | None:
	if not is_valid_unified_person_number(person_number):
		return None
	return _eligible_person(_canonical_person_row(person_number))


def persons_for_records(ccd_master_records: list[str]) -> dict[str, dict]:
	"""Resolve and vet a bounded record batch with four set-based queries."""
	record_names = sorted({str(value) for value in ccd_master_records if str(value)})
	if not record_names:
		return {}
	placeholders = ", ".join(["%s"] * len(record_names))
	direct_rows = frappe.db.sql(
		f"""
		SELECT m.ccd_master, p.name, p.unified_person_number, p.status,
		       p.canonical_person, p.current_identity_group,
		       g.status AS current_group_status
		  FROM `tabCCD Unified Person Membership` m
		  JOIN `tabCCD Unified Person` p ON p.name = m.unified_person
		  LEFT JOIN `tabCCD Identity Group` g ON g.name = p.current_identity_group
		 WHERE m.ccd_master IN ({placeholders}) AND m.status = 'Active'
		 ORDER BY m.ccd_master, p.name
		""",
		record_names,
		as_dict=True,
	)
	rows_by_record: dict[str, list[Any]] = {record: [] for record in record_names}
	for row in direct_rows:
		rows_by_record.setdefault(str(row.ccd_master), []).append(row)
	person_rows: dict[str, dict] = {}
	for rows in rows_by_record.values():
		if len(rows) != 1:
			continue
		row = dict(rows[0])
		if row.get("status") == "Active" and not row.get("canonical_person"):
			person_rows[row["name"]] = row
	if not person_rows:
		return {}
	person_names = sorted(person_rows)
	person_placeholders = ", ".join(["%s"] * len(person_names))
	person_memberships = frappe.db.sql(
		f"""
		SELECT m.name, m.unified_person, m.ccd_master
		  FROM `tabCCD Unified Person Membership` m
		  JOIN `tabCCD Master` c ON c.name = m.ccd_master
		 WHERE m.unified_person IN ({person_placeholders}) AND m.status = 'Active'
		 ORDER BY m.unified_person, m.ccd_master, m.name
		""",
		person_names,
		as_dict=True,
	)
	memberships_by_person: dict[str, list[Any]] = {person: [] for person in person_names}
	for row in person_memberships:
		memberships_by_person.setdefault(str(row.unified_person), []).append(row)
	member_records = sorted({str(row.ccd_master) for row in person_memberships})
	if not member_records:
		return {}
	member_placeholders = ", ".join(["%s"] * len(member_records))
	all_unified = frappe.db.sql(
		f"""
		SELECT name, unified_person, ccd_master
		  FROM `tabCCD Unified Person Membership`
		 WHERE status = 'Active' AND ccd_master IN ({member_placeholders})
		 ORDER BY ccd_master, name
		""",
		member_records,
		as_dict=True,
	)
	identity_rows = frappe.db.sql(
		f"""
		SELECT im.ccd_master, im.identity_group, im.status AS membership_status,
		       g.status AS group_status
		  FROM `tabCCD Identity Membership` im
		  LEFT JOIN `tabCCD Identity Group` g ON g.name = im.identity_group
		 WHERE im.ccd_master IN ({member_placeholders})
		   AND im.status IN ('Active', 'Needs Revalidation')
		 ORDER BY im.ccd_master, im.identity_group
		""",
		member_records,
		as_dict=True,
	)
	unified_by_record: dict[str, list[Any]] = {}
	identity_by_record: dict[str, list[Any]] = {}
	for row in all_unified:
		unified_by_record.setdefault(str(row.ccd_master), []).append(row)
	for row in identity_rows:
		identity_by_record.setdefault(str(row.ccd_master), []).append(row)
	eligible_people: dict[str, dict] = {}
	for person_name, person in person_rows.items():
		memberships = memberships_by_person.get(person_name, [])
		person_record_names = sorted({str(row.ccd_master) for row in memberships})
		current_unified = [
			row for record in person_record_names for row in unified_by_record.get(record, [])
		]
		current_identity = [
			row for record in person_record_names for row in identity_by_record.get(record, [])
		]
		eligible, _ = assignment_is_eligible(
			person,
			current_unified,
			current_identity,
			current_group_status=person.get("current_group_status"),
		)
		if eligible:
			eligible_people[person_name] = {**person, "ccd_master_records": person_record_names}
	result = {}
	for record, rows in rows_by_record.items():
		if len(rows) == 1 and rows[0].name in eligible_people:
			result[record] = eligible_people[rows[0].name]
	return result


def person_for_record(ccd_master_record: str) -> dict | None:
	return persons_for_records([ccd_master_record]).get(str(ccd_master_record))


def opaque_person_id(person_name: str) -> str:
	return "ccdu_" + protected_digest(person_name, "portal-person")[:40]


def identity_coverage(settings, policy: dict) -> dict[str, Any]:
	"""Aggregate-only V2 readiness for the Access Administrator coverage view."""
	status = schema_status()
	base = {
		"identity_schema": status,
		"portal_records_with_active_unified_assignments": 0,
		"eligible_multi_record_people": 0,
		"revalidation_suppressed_people": 0,
		"affected_anomalies": 0,
		"same_person_policy_active": policy_supports_same_person(policy),
		"same_person_global_enabled": bool(cint(getattr(settings, "same_person_enabled", 0))),
		"same_person_pilot_count": len(getattr(settings, "same_person_pilot_users", None) or []),
		"last_check": now_datetime(),
	}
	if not status["ready"]:
		return base
	assigned = frappe.db.sql(
		"""
		SELECT COUNT(DISTINCT r.name)
		  FROM `tabCCD Portal Record` r
		  JOIN `tabCCD Unified Person Membership` um
		    ON um.ccd_master = r.ccd_master_record AND um.status = 'Active'
		 WHERE r.active = 1
		"""
	)[0][0]
	rows = frappe.db.sql(
		"""
		SELECT
		  SUM(CASE WHEN member_count >= 2 AND duplicate_membership = 0
		            AND revalidation = 0 AND identity_group_count <= 1 THEN 1 ELSE 0 END) AS eligible_multi,
		  SUM(CASE WHEN revalidation > 0 THEN 1 ELSE 0 END) AS revalidation_people,
		  SUM(CASE WHEN duplicate_membership > 0 OR identity_group_count > 1 THEN 1 ELSE 0 END) AS anomalies
		FROM (
		  SELECT p.name,
		         COUNT(DISTINCT um.ccd_master) AS member_count,
		         MAX(CASE WHEN current_membership.active_count > 1 THEN 1 ELSE 0 END) AS duplicate_membership,
		         MAX(CASE WHEN im.status = 'Needs Revalidation' OR ig.status = 'Needs Revalidation'
		                  OR current_group.status = 'Needs Revalidation' THEN 1 ELSE 0 END) AS revalidation,
		         COUNT(DISTINCT CASE WHEN ig.status = 'Active' THEN ig.name END) AS identity_group_count
		    FROM `tabCCD Unified Person` p
		    JOIN `tabCCD Unified Person Membership` um
		      ON um.unified_person = p.name AND um.status = 'Active'
		    JOIN `tabCCD Master` c ON c.name = um.ccd_master
		    LEFT JOIN (
		      SELECT ccd_master, COUNT(*) AS active_count
		        FROM `tabCCD Unified Person Membership`
		       WHERE status = 'Active'
		       GROUP BY ccd_master
		    ) current_membership ON current_membership.ccd_master = um.ccd_master
		    LEFT JOIN `tabCCD Identity Membership` im
		      ON im.ccd_master = um.ccd_master AND im.status IN ('Active', 'Needs Revalidation')
		    LEFT JOIN `tabCCD Identity Group` ig ON ig.name = im.identity_group
		    LEFT JOIN `tabCCD Identity Group` current_group ON current_group.name = p.current_identity_group
		   WHERE p.status = 'Active' AND p.canonical_person IS NULL
		   GROUP BY p.name
		) governed_people
		""",
		as_dict=True,
	)[0]
	base.update(
		{
			"portal_records_with_active_unified_assignments": int(assigned or 0),
			"eligible_multi_record_people": int(rows.eligible_multi or 0),
			"revalidation_suppressed_people": int(rows.revalidation_people or 0),
			"affected_anomalies": int(rows.anomalies or 0),
		}
	)
	return base
