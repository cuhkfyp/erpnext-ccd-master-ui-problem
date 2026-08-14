from __future__ import annotations

import json
import re
import unicodedata
from datetime import date


def canonical_json(value) -> str:
	return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


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
