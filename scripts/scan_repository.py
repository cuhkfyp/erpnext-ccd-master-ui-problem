#!/usr/bin/env python3
"""Small offline guard; CI also runs gitleaks for a broader secret scan."""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "node_modules", "dist"}
PATTERNS = {
	"private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
	"generic API secret": re.compile(r"(?i)(?:api[_-]?secret|password)\s*[:=]\s*['\"][^'\"]{12,}"),
	"Hong Kong ID-like value": re.compile(r"\b[A-Z]{1,2}\d{6}\([0-9A]\)"),
}
ALLOW = {"A123456(3)"}  # Published algorithm test value; never a fixture identity.

failures = []
for path in ROOT.rglob("*"):
	if not path.is_file() or any(part in SKIP for part in path.parts):
		continue
	try:
		text = path.read_text(encoding="utf-8")
	except UnicodeDecodeError:
		continue
	for label, pattern in PATTERNS.items():
		for match in pattern.finditer(text):
			if match.group(0) not in ALLOW:
				failures.append(f"{path.relative_to(ROOT)}: {label}")
if failures:
	print("\n".join(failures))
	sys.exit(1)
print("repository scan passed")
