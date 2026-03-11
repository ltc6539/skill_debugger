from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from skill_debugger.skill_linter import lint_skill_directory
from skill_debugger.tool_registry import GENERIC_OBJECT_SCHEMA, normalize_tool_name


FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(?P<frontmatter>.*?)(?:\r?\n)---[ \t]*(?:\r?\n|$)",
    re.DOTALL,
)


def slugify(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return lowered.strip("-") or "skill"


@dataclass
class UploadedSkillMeta:
    skill_id: str
    name: str
    description: str
    full_content: str
    declared_tools: list[str]
    tool_definitions: list["UploadedToolDefinition"] = field(default_factory=list)
    legacy_id: str | None = None
    source_path: str | None = None
    lint: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "declared_tools": list(self.declared_tools),
            "allowed_tools": list(self.declared_tools),
            "tool_definitions": [item.to_dict() for item in self.tool_definitions],
            "legacy_id": self.legacy_id,
            "source_path": self.source_path,
            "lint": self.lint or {"valid": True, "errors": [], "warnings": []},
        }


@dataclass(frozen=True)
class UploadedToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class UploadedSkillRegistry:
    def __init__(self, skills_dir: Path):
        self._dir = Path(skills_dir)
        self._skills: dict[str, UploadedSkillMeta] = {}
        self._aliases: dict[str, UploadedSkillMeta] = {}
        self._ordered: list[UploadedSkillMeta] = []

    @property
    def skills(self) -> list[UploadedSkillMeta]:
        return list(self._ordered)

    def load_all(self) -> None:
        self._skills.clear()
        self._aliases.clear()
        self._ordered.clear()
        if not self._dir.exists():
            return
        for path in sorted(self._dir.glob("*/SKILL.md")):
            text = path.read_text(encoding="utf-8")
            meta = self.parse_skill_text(text, fallback_name=path.parent.name, source_path=str(path))
            meta.lint = lint_skill_directory(path.parent).to_dict()
            self._skills[meta.skill_id] = meta
            if meta.legacy_id:
                self._aliases[meta.legacy_id] = meta
            self._ordered.append(meta)

    @classmethod
    def parse_skill_text(
        cls,
        text: str,
        *,
        fallback_name: str,
        source_path: str | None = None,
    ) -> UploadedSkillMeta:
        frontmatter, body = cls._parse_frontmatter(text)
        raw_name = str(
            frontmatter.get("name")
            or frontmatter.get("id")
            or frontmatter.get("skill_id")
            or frontmatter.get("skill-id")
            or fallback_name
        ).strip()
        description = str(frontmatter.get("description") or "").strip()
        legacy_id = frontmatter.get("legacy_id") or frontmatter.get("legacy-id")
        tool_definitions = cls._parse_tool_definitions(
            frontmatter.get("tools")
            or frontmatter.get("tool-definitions")
            or frontmatter.get("tool_definitions")
        )
        declared_tools = cls._merge_tool_names(
            cls._parse_tools(frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools")),
            [item.name for item in tool_definitions],
        )
        skill_id = slugify(
            str(frontmatter.get("id") or frontmatter.get("skill_id") or frontmatter.get("skill-id") or raw_name)
        )
        return UploadedSkillMeta(
            skill_id=skill_id,
            name=raw_name,
            description=description,
            full_content=body,
            declared_tools=declared_tools,
            tool_definitions=tool_definitions,
            legacy_id=str(legacy_id).strip() if legacy_id else None,
            source_path=source_path,
        )

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
        if not text.startswith("---"):
            return {}, text
        match = FRONTMATTER_PATTERN.match(text)
        if match is None:
            return {}, text
        fm = match.group("frontmatter")
        body = text[match.end() :].lstrip("\n")
        try:
            meta = yaml.safe_load(fm) or {}
        except yaml.YAMLError:
            return {}, text
        if not isinstance(meta, dict):
            return {}, body
        return meta, body

    @staticmethod
    def _parse_tools(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            inner = text[1:-1]
            return [part.strip().strip("'").strip('"') for part in inner.split(",") if part.strip()]
        return [text]

    @classmethod
    def _parse_tool_definitions(cls, value: Any) -> list[UploadedToolDefinition]:
        if value is None:
            return []

        items: list[UploadedToolDefinition] = []
        raw_entries: list[tuple[str, Any]] = []

        if isinstance(value, dict):
            raw_entries = [(str(name), payload) for name, payload in value.items()]
        elif isinstance(value, list):
            for payload in value:
                if not isinstance(payload, dict):
                    continue
                raw_entries.append((str(payload.get("name") or ""), payload))
        else:
            return []

        for raw_name, payload in raw_entries:
            tool_name = normalize_tool_name(raw_name)
            if not tool_name:
                continue
            if isinstance(payload, dict):
                description = str(payload.get("description") or "").strip()
                schema_value = (
                    payload.get("input_schema")
                    or payload.get("input-schema")
                    or payload.get("schema")
                )
            else:
                description = ""
                schema_value = None
            items.append(
                UploadedToolDefinition(
                    name=tool_name,
                    description=description,
                    input_schema=cls._normalize_input_schema(schema_value),
                )
            )
        return items

    @staticmethod
    def _normalize_input_schema(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return dict(GENERIC_OBJECT_SCHEMA)
        schema = dict(value)
        if schema.get("type") is None:
            schema["type"] = "object"
        if schema.get("type") == "object":
            if not isinstance(schema.get("properties"), dict):
                schema["properties"] = {}
            schema.setdefault("additionalProperties", True)
        return schema

    @staticmethod
    def _merge_tool_names(*tool_lists: list[str]) -> list[str]:
        merged: list[str] = []
        for tool_list in tool_lists:
            for raw_name in tool_list:
                tool_name = normalize_tool_name(raw_name)
                if tool_name and tool_name not in merged:
                    merged.append(tool_name)
        return merged

    def resolve_skill_ids(self, skill_ids: list[str]) -> list[str]:
        resolved: list[str] = []
        for skill_id in skill_ids:
            key = str(skill_id).strip()
            if not key:
                continue
            meta = self._skills.get(key) or self._aliases.get(key)
            normalized = meta.skill_id if meta else slugify(key)
            if normalized not in resolved:
                resolved.append(normalized)
        return resolved

    def get_allowed_tools(self, skill_ids: list[str]) -> list[str]:
        allowed: list[str] = []
        for skill_id in self.resolve_skill_ids(skill_ids):
            meta = self._skills.get(skill_id)
            if not meta:
                continue
            for tool_name in meta.declared_tools:
                if tool_name not in allowed:
                    allowed.append(tool_name)
        return allowed

    def get_skill_content(self, skill_id: str) -> str:
        meta = self._skills.get(skill_id) or self._aliases.get(skill_id)
        return meta.full_content if meta else f"Skill not found: {skill_id}"

    def get_skill_meta(self, skill_id: str) -> UploadedSkillMeta | None:
        return self._skills.get(skill_id) or self._aliases.get(skill_id)

    def get_skills_content(self, skill_ids: list[str]) -> str:
        chunks: list[str] = []
        for skill_id in skill_ids:
            meta = self._skills.get(skill_id) or self._aliases.get(skill_id)
            if not meta:
                chunks.append(f"# [{skill_id}] Skill not found")
                continue
            chunks.append(f"# [{meta.skill_id}] {meta.name}\n\n{meta.full_content}")
        return "\n\n---\n\n".join(chunks)

    def get_skill_index_markdown(self, skill_ids: list[str] | None = None) -> str:
        rows = ["| Skill ID | Name | Description | Tools |", "|---|---|---|---|"]
        selected = self.resolve_skill_ids(skill_ids or [meta.skill_id for meta in self._ordered])
        for skill_id in selected:
            meta = self._skills.get(skill_id)
            if not meta:
                continue
            rows.append(
                f"| {meta.skill_id} | {meta.name} | {meta.description or '-'} | "
                f"{', '.join(meta.declared_tools) or '-'} |"
            )
        return "\n".join(rows)

    def has_skill(self, skill_id: str) -> bool:
        return (self._skills.get(skill_id) or self._aliases.get(skill_id)) is not None

    def list_skill_dicts(self) -> list[dict[str, Any]]:
        return [meta.to_dict() for meta in self._ordered]
