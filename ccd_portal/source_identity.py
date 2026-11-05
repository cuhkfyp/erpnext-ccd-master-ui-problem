from __future__ import annotations

import re


AMENDMENT_SUFFIX = re.compile(r"-\d+$")


def canonical_source_id(registration_name: str | None) -> str:
	"""Return the legacy stable source identity derived from a registration name.

	Frappe appends ``-<number>`` when a cancelled registration is amended.
	The existing agent deliberately removes that suffix before writing
	``CCD Master.ccd_reg_source``. Portal configuration still retains the full
	active registration name so its latest field mappings remain authoritative.
	"""
	return AMENDMENT_SUFFIX.sub("", str(registration_name or "").strip())


def stable_source_id(value: str | None) -> str:
	"""Normalize an already-stable agent source key without rewriting it."""
	return str(value or "").strip()


def source_id_for_registration(
	registration_name: str | None,
	stable_source_key: str | None = None,
) -> str:
	"""Prefer the agent's governed source key and retain legacy compatibility."""
	explicit = stable_source_id(stable_source_key)
	return explicit or canonical_source_id(registration_name)


def configured_source_id(registration_name: str | None) -> str:
	"""Resolve one submitted registration to the source stored on CCD Master.

	Newer connector registrations persist the durable identity in
	``ccd_stable_source_key``. Older registrations predate that field and retain
	the amendment-suffix convention.
	"""
	registration_name = stable_source_id(registration_name)
	if not registration_name:
		return ""

	import frappe

	meta = frappe.get_meta("CCD Registration")
	stable_source_key = (
		frappe.db.get_value("CCD Registration", registration_name, "ccd_stable_source_key")
		if meta.has_field("ccd_stable_source_key")
		else None
	)
	return source_id_for_registration(registration_name, stable_source_key)


def same_source_lineage(
	left: str | None,
	right: str | None,
	*,
	left_stable_source_key: str | None = None,
	right_stable_source_key: str | None = None,
) -> bool:
	left_id = source_id_for_registration(left, left_stable_source_key)
	right_id = source_id_for_registration(right, right_stable_source_key)
	return bool(left_id and left_id == right_id)
