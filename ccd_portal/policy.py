from __future__ import annotations

import frappe
from frappe import _

from ccd_portal.security import split_csv


def get_active_policy() -> tuple[dict, list[dict]]:
	policies = frappe.get_all(
		"CCD Portal Policy",
		filters={"status": "Active"},
		fields=["name", "policy_version", "title", "activated_on"],
		order_by="activated_on desc",
		limit=2,
	)
	if len(policies) != 1:
		frappe.throw(_("Exactly one active CCD Portal policy is required."), frappe.ValidationError)
	policy = policies[0]
	fields = frappe.get_all(
		"CCD Portal Field Policy",
		filters={"parent": policy.name, "parenttype": "CCD Portal Policy", "parentfield": "fields"},
		fields=[
			"fieldname",
			"label",
			"classification",
			"data_kind",
			"mask_strategy",
			"reveal_authorities",
			"searchable",
			"strong_identifier",
			"correctable",
			"display_order",
		],
		order_by="display_order asc, idx asc",
	)
	return dict(policy), [dict(row) for row in fields]


def policy_field_map() -> tuple[dict, dict[str, dict]]:
	policy, fields = get_active_policy()
	return policy, {row["fieldname"]: row for row in fields}


def reveal_allowed(field_policy: dict, authority: str) -> bool:
	return authority in split_csv(field_policy.get("reveal_authorities"))


def public_policy(policy: dict, fields: list[dict], authority: str) -> dict:
	return {
		"version": policy["policy_version"],
		"title": policy["title"],
		"fields": [
			{
				"fieldname": row["fieldname"],
				"label": row["label"],
				"classification": row["classification"],
				"data_kind": row["data_kind"],
				"searchable": bool(row["searchable"]),
				"strong_identifier": bool(row["strong_identifier"]),
				"correctable": bool(row["correctable"]),
				"revealable": reveal_allowed(row, authority),
				"display_order": row["display_order"],
			}
			for row in fields
		],
	}
