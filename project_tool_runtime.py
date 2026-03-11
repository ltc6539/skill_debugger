from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from skill_debugger.composio_support import load_composio_class
from skill_debugger.google_maps_tools import GOOGLE_MAPS_DIRECT_TOOLS, configure_google_maps
from skill_debugger.project_tool_catalog import PROJECT_TOOL_PRESETS
from skill_debugger.settings import SkillDebuggerSettings
from skill_debugger.tool_registry import GENERIC_OBJECT_SCHEMA, WorkspaceToolMeta


@dataclass
class ProjectToolHandle:
    name: str
    description: str
    input_schema: dict[str, Any]
    execution_mode: str
    source: str
    tool_obj: Any


class ProjectToolRuntime:
    def __init__(self, settings: SkillDebuggerSettings) -> None:
        self.settings = settings
        configure_google_maps(settings.google_maps_api_key)
        self._google_tools = {
            getattr(tool_obj, "name", ""): tool_obj for tool_obj in GOOGLE_MAPS_DIRECT_TOOLS
        }
        self._yelp_catalog = {
            meta.name: meta for meta in PROJECT_TOOL_PRESETS.get("yelp", [])
        }
        self._yelp_tools: dict[str, Any] | None = None
        self._yelp_load_error: Exception | None = None

    def get_live_handle(self, tool_name: str, *, allow_network: bool = True) -> ProjectToolHandle | None:
        google_tool = self._google_tools.get(tool_name)
        if google_tool is not None:
            return self._build_handle(
                tool_obj=google_tool,
                execution_mode="live_google_maps",
                source="project_catalog:google_maps",
            )

        if tool_name not in self._yelp_catalog:
            return None

        yelp_tool = self._load_yelp_tools().get(tool_name) if allow_network else None
        if yelp_tool is not None:
            return self._build_handle(
                tool_obj=yelp_tool,
                execution_mode="live_yelp",
                source="project_catalog:yelp",
            )

        yelp_meta = self._yelp_catalog.get(tool_name)
        if yelp_meta is None:
            return None
        return ProjectToolHandle(
            name=yelp_meta.name,
            description=yelp_meta.description,
            input_schema=copy.deepcopy(GENERIC_OBJECT_SCHEMA),
            execution_mode="live_yelp",
            source=yelp_meta.source,
            tool_obj=None,
        )

    def hydrate_meta(self, meta: WorkspaceToolMeta, *, allow_network: bool = False) -> WorkspaceToolMeta:
        handle = self.get_live_handle(meta.name, allow_network=allow_network)
        if handle is None:
            return meta
        return WorkspaceToolMeta(
            name=handle.name,
            description=handle.description or meta.description,
            execution_mode=handle.execution_mode,
            enabled=meta.enabled,
            input_schema=copy.deepcopy(handle.input_schema or meta.input_schema),
            source=handle.source,
        )

    async def execute(self, tool_name: str, payload: dict[str, Any]) -> str:
        handle = self.get_live_handle(tool_name, allow_network=True)
        if handle is None:
            raise KeyError(f"Live tool handler not found: {tool_name}")
        if handle.tool_obj is None:
            detail = f"{self._yelp_load_error}" if self._yelp_load_error else "tool metadata unavailable"
            raise RuntimeError(f"Live tool handler is unavailable for {tool_name}: {detail}")
        result = await handle.tool_obj.on_invoke_tool(None, json.dumps(payload, ensure_ascii=False))
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, indent=2)
        except TypeError:
            return json.dumps({"status": "ok", "tool": tool_name, "output": str(result)}, ensure_ascii=False, indent=2)

    def _load_yelp_tools(self) -> dict[str, Any]:
        if self._yelp_tools is not None:
            return self._yelp_tools
        if not self.settings.composio_api_key:
            self._yelp_tools = {}
            return self._yelp_tools
        try:
            Composio = load_composio_class(self.settings.composio_cache_dir)
            from composio_openai_agents import OpenAIAgentsProvider

            composio = Composio(
                api_key=self.settings.composio_api_key,
                provider=OpenAIAgentsProvider(),
            )
            self._yelp_tools = {
                getattr(tool_obj, "name", ""): tool_obj
                for tool_obj in composio.tools.get(
                    user_id=self.settings.composio_user_id,
                    toolkits=["yelp"],
                )
            }
            self._yelp_load_error = None
        except Exception as exc:
            self._yelp_load_error = exc
            self._yelp_tools = {}
        return self._yelp_tools

    @staticmethod
    def _build_handle(tool_obj: Any, *, execution_mode: str, source: str) -> ProjectToolHandle:
        return ProjectToolHandle(
            name=str(getattr(tool_obj, "name", "")).strip(),
            description=str(getattr(tool_obj, "description", "")).strip(),
            input_schema=copy.deepcopy(getattr(tool_obj, "params_json_schema", None) or GENERIC_OBJECT_SCHEMA),
            execution_mode=execution_mode,
            source=source,
            tool_obj=tool_obj,
        )
