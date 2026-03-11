from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


GENERIC_OBJECT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}


def normalize_tool_name(value: str) -> str:
    return str(value or "").strip()


@dataclass
class WorkspaceToolMeta:
    name: str
    description: str = ""
    execution_mode: str = "stub"
    enabled: bool = True
    input_schema: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(GENERIC_OBJECT_SCHEMA))
    source: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "execution_mode": self.execution_mode,
            "enabled": self.enabled,
            "input_schema": copy.deepcopy(self.input_schema),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkspaceToolMeta":
        return cls(
            name=normalize_tool_name(str(payload.get("name") or "")),
            description=str(payload.get("description") or "").strip(),
            execution_mode=str(payload.get("execution_mode") or "stub").strip() or "stub",
            enabled=bool(payload.get("enabled", True)),
            input_schema=copy.deepcopy(payload.get("input_schema") or GENERIC_OBJECT_SCHEMA),
            source=str(payload.get("source") or "manual").strip() or "manual",
        )


class WorkspaceToolRegistry:
    def __init__(self, payload: dict[str, Any] | None = None):
        self._tools: dict[str, WorkspaceToolMeta] = {}
        self._ordered: list[WorkspaceToolMeta] = []
        if payload:
            self.load_payload(payload)

    @property
    def tools(self) -> list[WorkspaceToolMeta]:
        return list(self._ordered)

    def load_payload(self, payload: dict[str, Any]) -> None:
        self._tools.clear()
        self._ordered.clear()
        for item in payload.get("tools", []):
            meta = WorkspaceToolMeta.from_dict(item)
            if not meta.name:
                continue
            self._tools[meta.name] = meta
            self._ordered.append(meta)

    def to_payload(self) -> dict[str, Any]:
        return {"tools": [meta.to_dict() for meta in self._ordered]}

    def upsert(self, meta: WorkspaceToolMeta) -> None:
        tool_name = normalize_tool_name(meta.name)
        if not tool_name:
            raise ValueError("Tool name cannot be empty.")
        normalized = WorkspaceToolMeta(
            name=tool_name,
            description=meta.description,
            execution_mode=meta.execution_mode,
            enabled=meta.enabled,
            input_schema=copy.deepcopy(meta.input_schema),
            source=meta.source,
        )
        existing = self._tools.get(tool_name)
        if existing is None:
            self._ordered.append(normalized)
        else:
            index = self._ordered.index(existing)
            self._ordered[index] = normalized
        self._tools[tool_name] = normalized

    def delete(self, tool_name: str) -> None:
        key = normalize_tool_name(tool_name)
        existing = self._tools.get(key)
        if existing is None:
            raise KeyError(f"Tool not found: {tool_name}")
        self._ordered.remove(existing)
        del self._tools[key]

    def has_tool(self, tool_name: str) -> bool:
        return normalize_tool_name(tool_name) in self._tools

    def get(self, tool_name: str) -> WorkspaceToolMeta | None:
        return self._tools.get(normalize_tool_name(tool_name))

    def list_tool_dicts(self) -> list[dict[str, Any]]:
        return [meta.to_dict() for meta in self._ordered]
