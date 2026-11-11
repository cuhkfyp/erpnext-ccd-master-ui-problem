from __future__ import annotations

import frappe


SEARCH_ACTIVITY_KEY = "ccd_portal:live_search_active"
SEARCH_ACTIVITY_TTL_SECONDS = 30


def mark_search_activity() -> None:
	"""Give interactive searches priority over best-effort reconciliation work."""
	try:
		frappe.cache.set_value(
			SEARCH_ACTIVITY_KEY,
			"1",
			expires_in_sec=SEARCH_ACTIVITY_TTL_SECONDS,
		)
	except Exception:
		# Search availability must not depend on this best-effort scheduling hint.
		pass


def search_recently_active() -> bool:
	try:
		return bool(frappe.cache.get_value(SEARCH_ACTIVITY_KEY))
	except Exception:
		return False
