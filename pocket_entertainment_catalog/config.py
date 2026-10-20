"""Environment-backed configuration for the local server."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .env import load_env


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    public_host: str | None
    catalog_path: Path
    static_path: Path
    repository_path: Path
    work_path: Path | None = None


def load_settings(
    env_file: Path | str | None = None, *, environ: Mapping[str, str] | None = None
) -> Settings:
    values = load_env(
        PROJECT_ROOT / ".env" if env_file is None else Path(env_file), environ=environ
    )
    for suffix in ("HOST", "PORT", "PUBLIC_HOST", "DATA", "WORK_DATA"):
        previous = f"POCKET_CATALOG_{suffix}"
        current = f"POCKET_ENTERTAINMENT_CATALOG_{suffix}"
        if previous in values and current not in values:
            raise ValueError(f"Rename {previous} to {current} in your configuration.")

    def setting(name: str, default: str = "") -> str:
        return values.get(f"POCKET_ENTERTAINMENT_CATALOG_{name}", default).strip()

    host = setting("HOST", "0.0.0.0")
    if not host or any(character.isspace() for character in host):
        raise ValueError("POCKET_ENTERTAINMENT_CATALOG_HOST must be a hostname or IP address.")
    public_host = setting("PUBLIC_HOST") or None
    try:
        port = int(setting("PORT", "8766"))
    except ValueError as error:
        raise ValueError("POCKET_ENTERTAINMENT_CATALOG_PORT must be an integer.") from error
    if not 1 <= port <= 65535:
        raise ValueError("POCKET_ENTERTAINMENT_CATALOG_PORT must be between 1 and 65535.")

    catalog_setting = setting("DATA", "data/catalog.jsonl")
    if not catalog_setting:
        raise ValueError("POCKET_ENTERTAINMENT_CATALOG_DATA must be a file path.")
    catalog_path = Path(catalog_setting)
    if not catalog_path.is_absolute():
        catalog_path = PROJECT_ROOT / catalog_path

    work_setting = setting("WORK_DATA", "data/editing.sqlite3")
    if not work_setting:
        raise ValueError("POCKET_ENTERTAINMENT_CATALOG_WORK_DATA must be a file path.")
    work_path = Path(work_setting)
    if not work_path.is_absolute():
        work_path = PROJECT_ROOT / work_path

    if catalog_path.resolve() == work_path.resolve():
        raise ValueError("Journey and Work must use different data files.")
    return Settings(
        host=host,
        port=port,
        public_host=public_host,
        catalog_path=catalog_path.resolve(),
        static_path=(Path(__file__).resolve().parent / "static").resolve(),
        repository_path=PROJECT_ROOT.resolve(),
        work_path=work_path.resolve(),
    )
