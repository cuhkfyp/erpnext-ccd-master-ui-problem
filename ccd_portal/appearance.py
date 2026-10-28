from __future__ import annotations

import re


PORTAL_APPEARANCE_DEFAULTS = {
	"header": "#163D2A",
	"primary": "#1D5A3C",
	"page": "#F5F7F6",
	"page_accent": "#E3F0E8",
	"card": "#FFFFFF",
	"highlight": "#F4FAF6",
	"label": "#E8F1EB",
}

PORTAL_APPEARANCE_FIELDS = {
	key: f"appearance_{key}_color" for key in PORTAL_APPEARANCE_DEFAULTS
}

HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


def is_hex_color(value) -> bool:
	return bool(HEX_COLOR_PATTERN.fullmatch(str(value or "").strip()))


def normalize_hex_color(value: str) -> str:
	return str(value).strip().upper()


def portal_appearance(settings) -> dict[str, str]:
	"""Return only validated presentation values, falling back to the shipped palette."""
	result = {}
	for key, default in PORTAL_APPEARANCE_DEFAULTS.items():
		value = getattr(settings, PORTAL_APPEARANCE_FIELDS[key], None)
		result[key] = normalize_hex_color(value) if is_hex_color(value) else default
	return result


def validate_portal_appearance(values) -> dict[str, str]:
	if not isinstance(values, dict) or set(values) != set(PORTAL_APPEARANCE_DEFAULTS):
		raise ValueError("The complete portal palette is required.")
	if not all(is_hex_color(value) for value in values.values()):
		raise ValueError("Every portal colour must be a six-digit hexadecimal value.")
	return {key: normalize_hex_color(values[key]) for key in PORTAL_APPEARANCE_DEFAULTS}
