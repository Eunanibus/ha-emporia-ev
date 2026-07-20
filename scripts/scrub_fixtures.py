"""Scrub secrets from raw Emporia captures before they may be committed.

Reads tests/library/fixtures/raw/*.json, replaces every secret-bearing value
with a stable fake, writes the sanitized file to tests/library/fixtures/.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

RAW_DIR = Path("tests/library/fixtures/raw")
OUT_DIR = Path("tests/library/fixtures")

SECRET_KEYS = {
    "IdToken",
    "AccessToken",
    "RefreshToken",
    "id_token",
    "access_token",
    "refresh_token",
    "password",
    "email",
    "username",
    "firstName",
    "lastName",
}
SERIAL_KEYS = {"deviceGid", "manufacturerDeviceId", "serialNumber", "serial", "customerGid"}

FAKE_TOKEN = "FAKE_TOKEN"
FAKE_EMAIL = "user@example.com"
FAKE_SERIAL = "1111111111"

_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            if key in SECRET_KEYS:
                out[key] = FAKE_EMAIL if "email" in key.lower() else FAKE_TOKEN
            elif key in SERIAL_KEYS:
                out[key] = FAKE_SERIAL
            else:
                out[key] = _scrub(val)
        return out
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return _JWT_RE.sub(FAKE_TOKEN, value)
    return value


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_files = sorted(RAW_DIR.glob("*.json"))
    if not raw_files:
        raise SystemExit(f"No raw captures found in {RAW_DIR}")
    for raw in raw_files:
        data = json.loads(raw.read_text())
        scrubbed = _scrub(data)
        out_path = OUT_DIR / raw.name.replace(".raw.json", ".json")
        out_path.write_text(json.dumps(scrubbed, indent=2, sort_keys=True) + "\n")
        print(f"scrubbed {raw} -> {out_path}")


if __name__ == "__main__":
    main()
