"""Deployment preflight for preserving existing, site-local rate-limit windows."""
import frappe


def prepare_cache_protection():
	"""Run in a fresh bench process before migration or a whole-site cache clear.

	Older cached app_hooks may predate the protection. Invalidate only that hook
	cache, load the newly installed hooks, and fail before clearing anything else
	if the preservation rule is absent. Never read/restore/reset quota counters.
	"""
	from ccd_portal.hooks import persistent_cache_keys

	frappe.cache.delete_value("app_hooks")
	configured = set(frappe.get_hooks("persistent_cache_keys"))
	if not set(persistent_cache_keys).issubset(configured):
		raise RuntimeError("Portal quota cache protection is not loaded; refusing cache maintenance.")
	return {"quota_cache_protection_ready": True}
