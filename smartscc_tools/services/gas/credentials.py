"""Read private GAS credentials without putting them in the source tree."""

import os
from pathlib import Path


def load_gas_setting(environment_name: str, filename: str) -> str:
    key = os.environ.get(environment_name, "").strip()
    if key:
        return key
    base = Path(os.environ.get("APPDATA") or Path.home() / ".config")
    try:
        return (base / "smartscc_tools" / filename).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"Konfigurasi GAS lokal tidak dapat dibaca ({filename}).") from error


def load_gas_api_key() -> str:
    return load_gas_setting("SMARTSCC_GAS_API_KEY", "gas_api_key.txt")
