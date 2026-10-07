"""Pure same-person safety rules shared by runtime code and synthetic tests."""

from __future__ import annotations

from typing import Any, Iterable


def _value(row: Any, fieldname: str, default=None):
	if isinstance(row, dict):
		return row.get(fieldname, default)
	return getattr(row, fieldname, default)


def assignment_is_eligible(
	person: Any,
	memberships: list[Any],
	identity_rows: list[Any],
	*,
	current_group_status: str | None = None,
) -> tuple[bool, str]:
	"""Apply the person-scoped vetoes without consulting historical groups."""
	if _value(person, "status") != "Active" or _value(person, "canonical_person"):
		return False, "inactive_or_noncanonical_person"
	if not memberships:
		return False, "missing_active_membership"
	membership_records = [str(_value(row, "ccd_master") or "") for row in memberships]
	if any(not record for record in membership_records) or len(membership_records) != len(set(membership_records)):
		return False, "duplicate_unified_membership"
	if current_group_status == "Needs Revalidation":
		return False, "needs_revalidation"
	current_groups: set[str] = set()
	for row in identity_rows:
		membership_status = str(_value(row, "membership_status") or _value(row, "status") or "")
		group_status = str(_value(row, "group_status") or "")
		if membership_status == "Needs Revalidation" or group_status == "Needs Revalidation":
			return False, "needs_revalidation"
		group = str(_value(row, "identity_group") or "")
		# Ended and missing historical groups do not veto stable source-lineage reuse.
		if group and group_status == "Active":
			current_groups.add(group)
	if len(current_groups) > 1:
		return False, "conflicting_identity_groups"
	return True, "eligible"


def bounded_record_order(
	direct_ids: Iterable[str],
	related_groups: Iterable[Iterable[str]],
	maximum: int = 20,
) -> tuple[list[str], bool]:
	"""Deduplicate deterministically with direct matches ahead of siblings."""
	ordered: list[str] = []
	seen: set[str] = set()
	for record_id in direct_ids:
		record_id = str(record_id)
		if record_id and record_id not in seen:
			seen.add(record_id)
			ordered.append(record_id)
	for group in related_groups:
		for record_id in sorted({str(value) for value in group if str(value)}):
			if record_id not in seen:
				seen.add(record_id)
				ordered.append(record_id)
	return ordered[:maximum], len(ordered) > maximum
