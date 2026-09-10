"""Environment-backed configuration for the local server."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    public_host: str | None
    catalog_path: Path
    static_path: Path
    repository_path: Path


def load_settings() -> Settings:
    values = _dotenv(PROJECT_ROOT / ".env")

    def setting(name: str, default: str = "") -> str:
        return os.environ.get(name, values.get(name, default)).strip()

    host = setting("POCKET_CATALOG_HOST", "0.0.0.0")
    public_host = setting("POCKET_CATALOG_PUBLIC_HOST") or None
    try:
        port = int(setting("POCKET_CATALOG_PORT", "8766"))
    except ValueError as error:
        raise ValueError("POCKET_CATALOG_PORT must be an integer.") from error
    if not 1 <= port <= 65535:
        raise ValueError("POCKET_CATALOG_PORT must be between 1 and 65535.")

    catalog_setting = setting("POCKET_CATALOG_DATA", "data/catalog.jsonl")
    catalog_path = Path(catalog_setting)
    if not catalog_path.is_absolute():
        catalog_path = PROJECT_ROOT / catalog_path

    return Settings(
        host=host,
        port=port,
        public_host=public_host,
        catalog_path=catalog_path.resolve(),
        static_path=(Path(__file__).resolve().parent / "static").resolve(),
        repository_path=PROJECT_ROOT.resolve(),
    )


def _dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env entry on line {line_number}.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        result[key] = value
    return result

