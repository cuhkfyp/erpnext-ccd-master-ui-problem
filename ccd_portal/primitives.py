from __future__ import annotations

import json
import re
import unicodedata
from datetime import date


UNIFIED_PERSON_PATTERN = re.compile(r"^HKSR-U([0-9]{9})([0-9])$")
SERVICE_FALLBACK_UNSPECIFIED = "Service not specified"
SERVICE_FALLBACK_SOURCE = "CCD database/source name"
SERVICE_NAME_FALLBACKS = frozenset({SERVICE_FALLBACK_UNSPECIFIED, SERVICE_FALLBACK_SOURCE})


def canonical_json(value) -> str:
	return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def resolve_service_name(
	record_service_name,
	registration_service_name,
	source_name,
	fallback_mode: str,
) -> str:
	"""Resolve a display-only service label without exposing a source by default."""
	for value in (record_service_name, registration_service_name):
		label = str(value or "").strip()
		if label:
			return label
	if fallback_mode == SERVICE_FALLBACK_SOURCE:
		label = str(source_name or "").strip()
		if label:
			return label
	return SERVICE_FALLBACK_UNSPECIFIED


def is_valid_source_column(value: str) -> bool:
	"""Accept only an unquoted SQL identifier that the existing agent can select safely."""
	return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,139}", str(value or "")))


def unified_person_check_digit(payload: str) -> int:
	"""Return the Luhn check digit for a nine-digit Unified Person payload."""
	payload = str(payload or "")
	if len(payload) != 9 or not payload.isdigit():
		raise ValueError("Unified Person payload must contain nine digits")
	digits = payload + "0"
	total = 0
	parity = len(digits) % 2
	for index, character in enumerate(digits):
		value = int(character)
		if index % 2 == parity:
			value *= 2
			if value > 9:
				value -= 9
		total += value
	return (10 - total % 10) % 10


def is_valid_unified_person_number(value: str) -> bool:
	"""Validate the canonical ``HKSR-U#########C`` format and check digit."""
	match = UNIFIED_PERSON_PATTERN.fullmatch(str(value or "").strip())
	if not match:
		return False
	payload, supplied = match.groups()
	return int(payload) > 0 and unified_person_check_digit(payload) == int(supplied)


def normalize_value(value, kind: str) -> str:
	raw = unicodedata.normalize("NFKC", str(value or "")).strip()
	if kind == "Date":
		return date.fromisoformat(raw).isoformat() if raw else ""
	if kind == "Email":
		return raw.casefold()
	if kind == "Phone":
		return re.sub(r"\D", "", raw)
	if kind in {"HKID", "Birth Certificate", "Identifier", "Staff ID"}:
		return re.sub(r"[^0-9A-Za-z]", "", raw).upper()
	return " ".join(raw.casefold().split())


def is_valid_hkid(value: str) -> bool:
	value = normalize_value(value, "HKID")
	match = re.fullmatch(r"([A-Z]{1,2})(\d{6})([0-9A])", value)
	if not match:
		return False
	letters, digits, check = match.groups()
	values = [36, ord(letters[0]) - 55] if len(letters) == 1 else [ord(letters[0]) - 55, ord(letters[1]) - 55]
	weighted = values[0] * 9 + values[1] * 8
	weighted += sum(int(digit) * weight for digit, weight in zip(digits, range(7, 1, -1), strict=True))
	check_value = 10 if check == "A" else int(check)
	return (weighted + check_value) % 11 == 0


def mask_value(value, strategy: str) -> str:
	text = str(value or "")
	if not text:
		return ""
	if strategy == "Last 4":
		return "•" * max(4, len(text) - 4) + text[-4:]
	if strategy == "Email":
		local, separator, domain = text.partition("@")
		return (local[:1] or "•") + "•••@" + domain if separator else "••••"
	if strategy == "Phone":
		digits = re.sub(r"\D", "", text)
		return "•••• " + digits[-4:] if len(digits) >= 4 else "••••"
	if strategy == "Initials":
		return " ".join(part[:1] + "••" for part in text.split())
	if strategy == "Year Only":
		try:
			return str(date.fromisoformat(text).year)
		except (TypeError, ValueError):
			return "••••"
	if strategy == "First Character":
		return text[:1] + "•" * max(2, len(text) - 1)
	return "••••"
