from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-opus-4.6"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def normalize_openrouter_base_url(url: str | None) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        return DEFAULT_OPENROUTER_BASE_URL
    if value.endswith("/api/v1"):
        return value[:-3]
    if value.endswith("/v1") and "/api" in value:
        return value[:-3]
    return value


@dataclass(frozen=True)
class SkillDebuggerSettings:
    env_file: Path
    openrouter_api_key: str | None
    openrouter_base_url: str
    default_model: str | None
    vlm_model: str | None = None
    google_maps_api_key: str | None = None
    composio_api_key: str | None = None
    composio_user_id: str = "default"
    composio_cache_dir: Path | None = None

    @property
    def openrouter_enabled(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def google_maps_enabled(self) -> bool:
        return bool(self.google_maps_api_key)

    @property
    def composio_enabled(self) -> bool:
        return bool(self.composio_api_key)

    def runtime_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.openrouter_enabled:
            env.update(
                {
                    "OPENROUTER_API_KEY": self.openrouter_api_key or "",
                    "OPENROUTER_BASE_URL": self.openrouter_base_url,
                    "ANTHROPIC_BASE_URL": self.openrouter_base_url,
                    "ANTHROPIC_AUTH_TOKEN": self.openrouter_api_key or "",
                    "ANTHROPIC_API_KEY": "",
                }
            )
        if self.default_model:
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = self.default_model
        if self.vlm_model:
            env["SKILL_DEBUGGER_VLM_MODEL"] = self.vlm_model
        if self.google_maps_api_key:
            env["GOOGLE_MAPS_API_KEY"] = self.google_maps_api_key
        if self.composio_api_key:
            env["COMPOSIO_API_KEY"] = self.composio_api_key
        if self.composio_user_id:
            env["COMPOSIO_USER_ID"] = self.composio_user_id
        if self.composio_cache_dir:
            env["COMPOSIO_CACHE_DIR"] = str(self.composio_cache_dir)
        return env

    def runtime_status(self) -> dict[str, str | bool | None]:
        return {
            "openrouter_enabled": self.openrouter_enabled,
            "openrouter_base_url": self.openrouter_base_url if self.openrouter_enabled else None,
            "default_model": self.default_model,
            "vlm_model": self.vlm_model,
            "google_maps_enabled": self.google_maps_enabled,
            "composio_enabled": self.composio_enabled,
            "composio_user_id": self.composio_user_id if self.composio_enabled else None,
            "composio_cache_dir": str(self.composio_cache_dir) if self.composio_cache_dir else None,
            "env_file": str(self.env_file),
        }


def load_skill_debugger_settings(base_dir: Path) -> SkillDebuggerSettings:
    env_file = Path(base_dir) / ".env"
    env_values = _parse_env_file(env_file)
    merged = {**env_values, **os.environ}
    openrouter_api_key = (merged.get("OPENROUTER_API_KEY") or "").strip() or None
    openrouter_base_url = normalize_openrouter_base_url(merged.get("OPENROUTER_BASE_URL"))
    default_model = (
        (merged.get("SKILL_DEBUGGER_MODEL") or "").strip()
        or (merged.get("OPENROUTER_MODEL") or "").strip()
        or (DEFAULT_OPENROUTER_MODEL if openrouter_api_key else "")
    ) or None
    vlm_model = (
        (merged.get("SKILL_DEBUGGER_VLM_MODEL") or "").strip()
        or (merged.get("OPENROUTER_VLM_MODEL") or "").strip()
        or ""
    ) or None
    google_maps_api_key = (merged.get("GOOGLE_MAPS_API_KEY") or "").strip() or None
    composio_api_key = (merged.get("COMPOSIO_API_KEY") or "").strip() or None
    composio_user_id = (merged.get("COMPOSIO_USER_ID") or "").strip() or "default"
    composio_cache_dir = Path(
        (merged.get("COMPOSIO_CACHE_DIR") or "").strip()
        or (base_dir / ".composio_cache")
    )

    settings = SkillDebuggerSettings(
        env_file=env_file,
        openrouter_api_key=openrouter_api_key,
        openrouter_base_url=openrouter_base_url,
        default_model=default_model,
        vlm_model=vlm_model,
        google_maps_api_key=google_maps_api_key,
        composio_api_key=composio_api_key,
        composio_user_id=composio_user_id,
        composio_cache_dir=composio_cache_dir,
    )

    for key, value in settings.runtime_env().items():
        os.environ[key] = value

    settings.composio_cache_dir.mkdir(parents=True, exist_ok=True)

    # Remove CLAUDECODE so the spawned claude CLI does not think
    # it is running inside a nested Claude Code session.
    os.environ.pop("CLAUDECODE", None)

    return settings
