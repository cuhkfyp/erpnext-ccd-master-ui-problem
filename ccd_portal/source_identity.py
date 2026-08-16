from __future__ import annotations

import re


AMENDMENT_SUFFIX = re.compile(r"-\d+$")


def canonical_source_id(registration_name: str | None) -> str:
	"""Return the stable source identity used by the existing CCD agent.

	Frappe appends ``-<number>`` when a cancelled registration is amended.
	The existing agent deliberately removes that suffix before writing
	``CCD Master.ccd_reg_source``. Portal configuration still retains the full
	active registration name so its latest field mappings remain authoritative.
	"""
	return AMENDMENT_SUFFIX.sub("", str(registration_name or "").strip())


def same_source_lineage(left: str | None, right: str | None) -> bool:
	left_id = canonical_source_id(left)
	return bool(left_id and left_id == canonical_source_id(right))
