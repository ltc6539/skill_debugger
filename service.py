from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import re
import shutil
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any, Literal

logger = logging.getLogger(__name__)

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool
from claude_agent_sdk._errors import (
    CLIConnectionError,
    CLINotFoundError,
    ClaudeSDKError,
    ProcessError,
)
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from skill_debugger.runtime import ClaudeSdkRuntime
from skill_debugger.settings import SkillDebuggerSettings
from skill_debugger.project_tool_catalog import get_project_tool_metas, get_project_tool_preset_names
from skill_debugger.project_tool_runtime import ProjectToolRuntime
from skill_debugger.reviewer import SkillReviewer
from skill_debugger.skill_linter import SkillLintReport, lint_skill_package
from skill_debugger.skill_registry import UploadedSkillRegistry, slugify
from skill_debugger.store import WorkspaceStore
from skill_debugger.tool_registry import (
    GENERIC_OBJECT_SCHEMA,
    WorkspaceToolMeta,
    WorkspaceToolRegistry,
    normalize_tool_name,
)

DebuggerMode = Literal["agent", "forced"]
EXPLICIT_BUILTIN_TOOLS = ["Skill"]
SKILL_PATH_PATTERN = re.compile(r"(?:^|[\\/])\.claude[\\/]skills[\\/](?P<skill>[^\\/]+)")


def _extract_text_delta(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    if event_type == "content_block_delta":
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            return str(delta.get("text") or "")
    if event_type == "content_block_start":
        block = event.get("content_block") or {}
        if block.get("type") == "text":
            return str(block.get("text") or "")
    return ""


def _maybe_parse_json_text(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped[0] in {"{", "["}:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return text
    return text


def _normalize_tool_result_content(content: str | list[dict[str, Any]] | None) -> Any:
    if content is None:
        return None
    if isinstance(content, str):
        return _maybe_parse_json_text(content)
    if (
        len(content) == 1
        and isinstance(content[0], dict)
        and content[0].get("type") == "text"
        and isinstance(content[0].get("text"), str)
    ):
        return _maybe_parse_json_text(str(content[0].get("text") or ""))
    return content


def _collect_string_leaves(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        leaves: list[str] = []
        for item in value.values():
            leaves.extend(_collect_string_leaves(item))
        return leaves
    if isinstance(value, list):
        leaves: list[str] = []
        for item in value:
            leaves.extend(_collect_string_leaves(item))
        return leaves
    return [str(value)]


def _extract_skill_hits(*payloads: Any) -> list[str]:
    found: list[str] = []
    for payload in payloads:
        if isinstance(payload, dict):
            explicit = str(payload.get("skill") or payload.get("skill_id") or "").strip()
            if explicit and explicit not in found:
                found.append(explicit)
        for text in _collect_string_leaves(payload):
            for match in SKILL_PATH_PATTERN.finditer(text):
                skill_name = match.group("skill").strip()
                if skill_name and skill_name not in found:
                    found.append(skill_name)
    return found


@dataclass
class ToolTraceCollector:
    trace_events: list[dict[str, Any]] = field(default_factory=list)
    pending_events: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_uses: dict[str, dict[str, Any]] = field(default_factory=dict)
    local_tool_executions: list[dict[str, Any]] = field(default_factory=list)

    def record_local_tool_execution(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        output: Any,
        status: str,
    ) -> None:
        self.local_tool_executions.append(
            {
                "tool": tool_name,
                "input": dict(tool_input),
                "output": output,
                "status": status,
            }
        )

    def record_tool_use(self, block: ToolUseBlock) -> None:
        self.pending_tool_uses[block.id] = {
            "tool_use_id": block.id,
            "tool": block.name,
            "input": dict(block.input),
        }

    def record_tool_result(self, block: ToolResultBlock) -> None:
        pending = self.pending_tool_uses.pop(
            block.tool_use_id,
            {
                "tool_use_id": block.tool_use_id,
                "tool": "unknown_tool",
                "input": None,
            },
        )
        output = _normalize_tool_result_content(block.content)
        event = {
            "trace_id": len(self.trace_events) + 1,
            "type": "tool_call",
            "tool": pending["tool"],
            "tool_use_id": block.tool_use_id,
            "status": "error" if block.is_error else "ok",
            "input": pending.get("input"),
            "output": output,
        }
        skill_hits = _extract_skill_hits(pending.get("input"), output)
        if pending["tool"] == "Skill" or skill_hits:
            event["category"] = "skill_activation"
            if skill_hits:
                event["skills"] = skill_hits
        self.trace_events.append(event)
        self.pending_events.append(event)

    def finalize_pending(self) -> None:
        if not self.pending_tool_uses:
            return
        for pending in list(self.pending_tool_uses.values()):
            local_execution = self._consume_local_tool_execution(
                pending["tool"],
                pending.get("input"),
            )
            event = {
                "trace_id": len(self.trace_events) + 1,
                "type": "tool_call",
                "tool": pending["tool"],
                "tool_use_id": pending["tool_use_id"],
                "status": local_execution.get("status", "ok") if local_execution else "ok",
                "input": pending.get("input"),
                "output": (
                    local_execution.get("output")
                    if local_execution
                    else {
                        "status": "completed_without_tool_result",
                        "message": "The SDK stream emitted the tool use, but no matching ToolResultBlock was surfaced.",
                        "inferred": True,
                    }
                ),
            }
            skill_hits = _extract_skill_hits(pending.get("input"))
            if pending["tool"] == "Skill" or skill_hits:
                event["category"] = "skill_activation"
                if skill_hits:
                    event["skills"] = skill_hits
            self.trace_events.append(event)
            self.pending_events.append(event)
        self.pending_tool_uses.clear()

    def drain_pending_events(self) -> list[dict[str, Any]]:
        items = list(self.pending_events)
        self.pending_events.clear()
        return items

    def _consume_local_tool_execution(
        self,
        tool_name: str,
        tool_input: Any,
    ) -> dict[str, Any] | None:
        for index, item in enumerate(self.local_tool_executions):
            if item["tool"] == tool_name and item["input"] == tool_input:
                return self.local_tool_executions.pop(index)
        for index, item in enumerate(self.local_tool_executions):
            if item["tool"] == tool_name:
                return self.local_tool_executions.pop(index)
        return None


def _sdk_workspace_tool_name(tool_name: str) -> str:
    return f"mcp__skill_debugger__{tool_name}"


@dataclass
class StubToolRuntime:
    workspace_skill_ids: list[str]
    visible_skill_ids: list[str]

    def invoke_stub_tool(self, tool_name: str, payload: dict[str, Any]) -> str:
        response = {
            "status": "stubbed",
            "tool": tool_name,
            "message": "Debug stub executed. No production backend call was made.",
            "received": payload,
            "visible_skills": list(self.visible_skill_ids),
            "workspace_skills": list(self.workspace_skill_ids),
        }
        return json.dumps(response, ensure_ascii=False, indent=2)


@dataclass
class SkillPackageUpload:
    package_name: str
    files: dict[str, bytes]
    source_kind: Literal["folder", "single_file"] = "folder"


class SkillDebuggerService:
    def __init__(
        self,
        *,
        store: WorkspaceStore,
        settings: SkillDebuggerSettings,
        runtime: ClaudeSdkRuntime | None = None,
        project_tool_runtime: ProjectToolRuntime | None = None,
        reviewer: SkillReviewer | None = None,
    ):
        self.store = store
        self.settings = settings
        self.runtime = runtime or ClaudeSdkRuntime()
        self.project_tool_runtime = project_tool_runtime or ProjectToolRuntime(settings)
        self.reviewer = reviewer or SkillReviewer()
        self._workspace_write_locks: dict[str, asyncio.Lock] = {}
        self._workspace_write_locks_guard = Lock()

    def _get_workspace_write_lock(self, workspace_id: str) -> asyncio.Lock:
        with self._workspace_write_locks_guard:
            lock = self._workspace_write_locks.get(workspace_id)
            if lock is None:
                lock = asyncio.Lock()
                self._workspace_write_locks[workspace_id] = lock
            return lock

    @asynccontextmanager
    async def _workspace_write(self, workspace_id: str):
        lock = self._get_workspace_write_lock(workspace_id)
        async with lock:
            yield

    def bootstrap(self) -> dict[str, Any]:
        current = self.store.ensure_default_workspace()
        workspaces = self.store.list_workspaces()
        return {
            "workspaces": workspaces,
            "current_workspace_id": current["workspace_id"],
            "current": self.get_workspace_state(current["workspace_id"]),
            "runtime": self.runtime_status(),
        }

    def runtime_status(self) -> dict[str, Any]:
        return {
            "claude_cli_path": shutil.which("claude"),
            "runtime_mode": "claude_native",
            "builtin_tools": list(EXPLICIT_BUILTIN_TOOLS),
            **self.settings.runtime_status(),
        }

    def create_workspace(self, name: str | None) -> dict[str, Any]:
        workspace = self.store.create_workspace(name)
        return self.get_workspace_state(workspace["workspace_id"])

    def get_workspace_state(self, workspace_id: str) -> dict[str, Any]:
        return self._get_workspace_state(workspace_id, persist_updates=False)

    def _get_workspace_state(
        self,
        workspace_id: str,
        *,
        persist_updates: bool,
    ) -> dict[str, Any]:
        workspace = self.store.get_workspace(workspace_id)
        registry = self._load_registry(workspace_id)
        tool_registry = self._sync_declared_skill_tools(
            workspace_id,
            registry=registry,
            persist_updates=persist_updates,
        )
        session = self.store.get_session(workspace_id)
        return {
            "workspace": workspace,
            "skills": registry.list_skill_dicts(),
            "tools": self._serialize_workspace_tools(registry, tool_registry),
            "unregistered_declared_tools": self._collect_unregistered_declared_tools(registry, tool_registry),
            "reviews": self.store.list_reviews(workspace_id),
            "session": session,
            "runtime": self.runtime_status(),
        }

    async def delete_workspace(self, workspace_id: str) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            self.store.delete_workspace(workspace_id)
            current = self.store.ensure_default_workspace()
            workspaces = self.store.list_workspaces()
            return {
                "deleted_workspace_id": workspace_id,
                "workspaces": workspaces,
                "current_workspace_id": current["workspace_id"],
                "current": self.get_workspace_state(current["workspace_id"]),
                "runtime": self.runtime_status(),
            }

    async def clear_context(self, workspace_id: str) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            self.store.clear_session(workspace_id)
            self.store.clear_reviews(workspace_id)
            return self._get_workspace_state(workspace_id, persist_updates=True)

    async def create_review(
        self,
        workspace_id: str,
        *,
        turn_id: str,
        skill_id: str | None = None,
        include_recent_turns: bool = True,
    ) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            workspace_state = self._get_workspace_state(workspace_id, persist_updates=True)
            session = workspace_state["session"]
            turns = list(session.get("turns") or [])
            turn_index = next(
                (index for index, item in enumerate(turns) if str(item.get("turn_id") or "") == turn_id),
                None,
            )
            if turn_index is None:
                raise KeyError(f"Turn not found: {turn_id}")

            turn = turns[turn_index]
            registry = self._load_registry(workspace_id)
            resolved_skill_id = self._resolve_review_skill_id(
                turn=turn,
                registry=registry,
                requested_skill_id=skill_id,
            )
            skill_meta = registry.get_skill_meta(resolved_skill_id)
            if skill_meta is None:
                raise KeyError(f"Skill not found: {resolved_skill_id}")

            recent_turns = turns[max(0, turn_index - 2) : turn_index] if include_recent_turns else []
            review = self.reviewer.review(
                turn=turn,
                recent_turns=recent_turns,
                skill=skill_meta.to_dict(),
                skill_document=self.store.read_skill_text(workspace_id, resolved_skill_id),
                tools=list(workspace_state.get("tools") or []),
                unregistered_declared_tools=list(workspace_state.get("unregistered_declared_tools") or []),
            )
            self.store.save_review(workspace_id, review)
            return review

    async def upload_image(
        self,
        workspace_id: str,
        *,
        filename: str,
        content: bytes,
        mime_type: str | None,
    ) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            if not content:
                raise ValueError("Uploaded image is empty.")
            if mime_type and not mime_type.startswith("image/"):
                raise ValueError(f"Only image uploads are supported, got: {mime_type}")
            return self.store.save_uploaded_image(
                workspace_id,
                filename=filename,
                content=content,
                mime_type=mime_type,
            )

    async def upload_skills(self, workspace_id: str, files: list[tuple[str, bytes]]) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            self.store.ensure_native_skill_layout(workspace_id)
            packages = self._build_skill_packages(files)
            if not packages:
                raise ValueError(
                    "No skill package detected. Upload a skill folder, a zip archive, or one or more SKILL.md files."
                )
            lint_reports = [
                lint_skill_package(
                    package.package_name,
                    package.files,
                    source_kind=package.source_kind,
                )
                for package in packages
            ]
            invalid_reports = [report for report in lint_reports if not report.valid]
            if invalid_reports:
                raise ValueError(self._format_upload_lint_errors(invalid_reports))
            for package in packages:
                skill_bytes = package.files.get("SKILL.md")
                if skill_bytes is None:
                    raise ValueError(f"Skill package is missing SKILL.md: {package.package_name}")
                text = skill_bytes.decode("utf-8")
                parsed = UploadedSkillRegistry.parse_skill_text(text, fallback_name=package.package_name)
                self.store.write_skill_package(workspace_id, parsed.skill_id, package.files)
            self.store.invalidate_runtime_session(workspace_id)
            self._reset_runtime_projects(workspace_id)
            return self._get_workspace_state(workspace_id, persist_updates=True)

    async def delete_skill(self, workspace_id: str, skill_id: str) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            normalized = slugify(skill_id)
            self.store.delete_skill(workspace_id, normalized)
            self.store.invalidate_runtime_session(workspace_id)
            self._reset_runtime_projects(workspace_id)
            return self._get_workspace_state(workspace_id, persist_updates=True)

    def get_skill_document(self, workspace_id: str, skill_id: str) -> dict[str, Any]:
        normalized = slugify(skill_id)
        registry = self._load_registry(workspace_id)
        meta = registry.get_skill_meta(normalized)
        if meta is None:
            raise KeyError(f"Skill not found: {skill_id}")
        content = self.store.read_skill_text(workspace_id, normalized)
        return {
            "skill": meta.to_dict(),
            "content": content,
        }

    async def update_skill_document(self, workspace_id: str, skill_id: str, content: str) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            normalized = slugify(skill_id)
            registry = self._load_registry(workspace_id)
            meta = registry.get_skill_meta(normalized)
            if meta is None:
                raise KeyError(f"Skill not found: {skill_id}")

            package_files = self.store.read_skill_package(workspace_id, normalized)
            package_files["SKILL.md"] = content.encode("utf-8")

            lint_report = lint_skill_package(
                normalized,
                package_files,
                source_kind="folder",
            )
            if not lint_report.valid:
                raise ValueError(self._format_upload_lint_errors([lint_report]))

            parsed = UploadedSkillRegistry.parse_skill_text(content, fallback_name=normalized)
            next_skill_id = parsed.skill_id
            if next_skill_id != normalized and registry.has_skill(next_skill_id):
                raise ValueError(f"Cannot rename skill to `{next_skill_id}` because that skill already exists.")

            self.store.write_skill_package(workspace_id, next_skill_id, package_files)
            if next_skill_id != normalized:
                self.store.delete_skill(workspace_id, normalized)

            self.store.invalidate_runtime_session(workspace_id)
            self._reset_runtime_projects(workspace_id)
            return {
                "updated_skill_id": next_skill_id,
                "previous_skill_id": normalized,
                "current": self._get_workspace_state(workspace_id, persist_updates=True),
            }

    async def add_tool(self, workspace_id: str, name: str, description: str | None = None) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            registry = self._load_tool_registry(workspace_id)
            tool_name = normalize_tool_name(name)
            if not tool_name:
                raise ValueError("Tool name cannot be empty.")
            meta = self.project_tool_runtime.hydrate_meta(
                WorkspaceToolMeta(
                    name=tool_name,
                    description=str(description or "").strip(),
                    execution_mode="stub",
                    enabled=True,
                    source="manual",
                )
            )
            registry.upsert(meta)
            self.store.save_tool_registry(workspace_id, registry.to_payload())
            self.store.invalidate_runtime_session(workspace_id)
            return self._get_workspace_state(workspace_id, persist_updates=True)

    async def delete_tool(self, workspace_id: str, tool_name: str) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            registry = self._load_tool_registry(workspace_id)
            registry.delete(tool_name)
            self.store.save_tool_registry(workspace_id, registry.to_payload())
            self.store.invalidate_runtime_session(workspace_id)
            return self._get_workspace_state(workspace_id, persist_updates=True)

    async def sync_project_tool_presets(
        self,
        workspace_id: str,
        preset_names: list[str] | None = None,
    ) -> dict[str, Any]:
        async with self._workspace_write(workspace_id):
            registry = self._load_tool_registry(workspace_id)
            selected = preset_names or get_project_tool_preset_names()
            for meta in get_project_tool_metas(selected):
                registry.upsert(self.project_tool_runtime.hydrate_meta(meta))
            self.store.save_tool_registry(workspace_id, registry.to_payload())
            self.store.invalidate_runtime_session(workspace_id)
            return self._get_workspace_state(workspace_id, persist_updates=True)

    async def run_chat(
        self,
        *,
        workspace_id: str,
        message: str,
        mode: DebuggerMode,
        forced_skill_id: str | None = None,
        model: str | None = None,
        image_ids: list[str] | None = None,
    ):
        async with self._workspace_write(workspace_id):
            prompt = message.strip()
            attached_images = self._load_attached_images(workspace_id, image_ids or [])
            if not prompt and not attached_images:
                raise ValueError("Message cannot be empty.")
            effective_prompt = self._build_prompt_with_attached_images(prompt, attached_images)

            registry = self._load_registry(workspace_id)
            workspace_skill_ids = [meta.skill_id for meta in registry.skills]
            visible_skill_ids = list(workspace_skill_ids)

            if mode == "forced":
                if not forced_skill_id:
                    raise ValueError("Forced mode requires a forced_skill_id.")
                forced_skill_id = slugify(forced_skill_id)
                if not registry.has_skill(forced_skill_id):
                    raise ValueError(f"Forced skill not found: {forced_skill_id}")
                visible_skill_ids = [forced_skill_id]

            runtime_project_dir = self._prepare_runtime_project(
                workspace_id=workspace_id,
                registry=registry,
                mode=mode,
                visible_skill_ids=visible_skill_ids,
            )
            runtime_cwd = str(runtime_project_dir.resolve())

            session = self.store.get_session(workspace_id)
            tool_registry = self._sync_declared_skill_tools(
                workspace_id,
                registry=registry,
                persist_updates=True,
            )
            stub_runtime = StubToolRuntime(
                workspace_skill_ids=workspace_skill_ids,
                visible_skill_ids=visible_skill_ids,
            )
            trace_collector = ToolTraceCollector()
            effective_model = model or self.settings.default_model
            resume_session_id = (
                session.get("claude_session_id")
                if self._can_resume_session(
                    session,
                    mode,
                    forced_skill_id,
                    effective_model,
                    runtime_cwd,
                )
                else None
            )

            current_options = self._build_runtime_options(
                workspace_id=workspace_id,
                tool_registry=tool_registry,
                tool_runtime=stub_runtime,
                trace_collector=trace_collector,
                mode=mode,
                visible_skill_ids=visible_skill_ids,
                runtime_project_dir=runtime_project_dir,
                effective_model=effective_model,
                resume_session_id=resume_session_id,
            )

            streamed_text_parts: list[str] = []
            final_text_parts: list[str] = []
            result_text: str | None = None
            claude_session_id = session.get("claude_session_id")

            yield {
                "event": "meta",
                "data": {
                    "mode": mode,
                    "forced_skill_id": forced_skill_id,
                    "model": effective_model,
                    "visible_skill_ids": visible_skill_ids,
                    "attached_images": attached_images,
                },
            }

            max_attempts = 2 if resume_session_id else 1
            succeeded = False

            for attempt in range(1, max_attempts + 1):
                try:
                    async for sdk_message in self.runtime.stream(effective_prompt, current_options):
                        if isinstance(sdk_message, StreamEvent):
                            delta = _extract_text_delta(sdk_message.event)
                            if delta:
                                streamed_text_parts.append(delta)
                                yield {"event": "token", "data": {"delta": delta}}

                        if isinstance(sdk_message, AssistantMessage):
                            for block in sdk_message.content:
                                if isinstance(block, TextBlock):
                                    final_text_parts.append(block.text)
                                elif isinstance(block, ToolUseBlock):
                                    trace_collector.record_tool_use(block)
                                elif isinstance(block, ToolResultBlock):
                                    trace_collector.record_tool_result(block)
                                elif isinstance(block, ThinkingBlock):
                                    continue

                        if isinstance(sdk_message, ResultMessage):
                            claude_session_id = sdk_message.session_id
                            result_text = sdk_message.result

                        for trace_event in trace_collector.drain_pending_events():
                            yield {"event": "trace", "data": trace_event}

                    succeeded = True
                    break
                except Exception as exc:
                    user_msg, is_retryable = self._classify_cli_error(exc)
                    logger.warning(
                        "CLI error (attempt %d/%d, retryable=%s): %s",
                        attempt, max_attempts, is_retryable, exc,
                    )

                    if is_retryable and attempt < max_attempts:
                        self.store.invalidate_runtime_session(workspace_id)
                        streamed_text_parts.clear()
                        final_text_parts.clear()
                        result_text = None
                        trace_collector = ToolTraceCollector()
                        current_options = self._build_runtime_options(
                            workspace_id=workspace_id,
                            tool_registry=tool_registry,
                            tool_runtime=stub_runtime,
                            trace_collector=trace_collector,
                            mode=mode,
                            visible_skill_ids=visible_skill_ids,
                            runtime_project_dir=runtime_project_dir,
                            effective_model=effective_model,
                            resume_session_id=None,
                        )
                        continue

                    yield {"event": "error", "data": {"message": user_msg}}
                    return

            if not succeeded:
                return

            trace_collector.finalize_pending()
            for trace_event in trace_collector.drain_pending_events():
                yield {"event": "trace", "data": trace_event}

            assistant_text = "".join(streamed_text_parts).strip()
            if not assistant_text:
                assistant_text = "".join(final_text_parts).strip() or (result_text or "").strip()

            session = self.store.append_turn(
                workspace_id,
                user_message=prompt,
                assistant_message=assistant_text,
                trace=trace_collector.trace_events,
                attached_images=attached_images,
                mode=mode,
                forced_skill_id=forced_skill_id,
                model=effective_model,
                claude_session_id=claude_session_id,
                runtime_cwd=runtime_cwd,
            )
            yield {
                "event": "done",
                "data": {
                    "assistant_message": assistant_text,
                    "trace": trace_collector.trace_events,
                    "session": session,
                },
            }

    @staticmethod
    def _classify_cli_error(exc: Exception) -> tuple[str, bool]:
        """Return (user_message, is_retryable) for a CLI error."""
        stderr_text = getattr(exc, "stderr", None) or ""
        exit_code = getattr(exc, "exit_code", None)

        if isinstance(exc, CLINotFoundError):
            return "Claude CLI is not installed or not found on this server.", False

        if isinstance(exc, CLIConnectionError):
            return "Unable to connect to Claude CLI. The service may be restarting.", True

        if isinstance(exc, ProcessError):
            lower = stderr_text.lower() if stderr_text else str(exc).lower()
            # Session / resume failures are retryable
            if "session" in lower or "resume" in lower or exit_code == 1:
                return "Session expired, retrying with a fresh session...", True
            if "api key" in lower or "auth" in lower or "unauthorized" in lower:
                return "Authentication error. Please check your API key configuration.", False
            if "rate limit" in lower or "429" in lower:
                return "Rate limited by the API. Please wait a moment and try again.", False
            # Generic process error — include stderr snippet for diagnosis
            snippet = (stderr_text[:200] + "...") if len(stderr_text) > 200 else stderr_text
            msg = f"CLI process failed (exit code {exit_code})."
            if snippet:
                msg += f" Details: {snippet}"
            return msg, False

        if isinstance(exc, ClaudeSDKError):
            return f"SDK error: {exc}", False

        return f"Unexpected error: {type(exc).__name__}", False

    def _load_registry(self, workspace_id: str) -> UploadedSkillRegistry:
        self.store.ensure_native_skill_layout(workspace_id)
        registry = UploadedSkillRegistry(self.store.skills_dir(workspace_id))
        registry.load_all()
        return registry

    @staticmethod
    def _resolve_review_skill_id(
        *,
        turn: dict[str, Any],
        registry: UploadedSkillRegistry,
        requested_skill_id: str | None,
    ) -> str:
        if requested_skill_id:
            normalized = slugify(requested_skill_id)
            if not registry.has_skill(normalized):
                raise KeyError(f"Skill not found: {requested_skill_id}")
            return normalized

        forced_skill_id = str(turn.get("forced_skill_id") or "").strip()
        if str(turn.get("mode") or "").strip() == "forced" and forced_skill_id:
            if registry.has_skill(forced_skill_id):
                return forced_skill_id

        activated_skills: list[str] = []
        for entry in turn.get("trace") or []:
            if entry.get("category") != "skill_activation":
                continue
            for raw in entry.get("skills") or []:
                value = str(raw or "").strip()
                if value and value not in activated_skills:
                    activated_skills.append(value)
            input_payload = entry.get("input") or {}
            explicit = str(input_payload.get("skill") or input_payload.get("skill_id") or "").strip()
            if explicit and explicit not in activated_skills:
                activated_skills.append(explicit)

        if len(activated_skills) == 1 and registry.has_skill(activated_skills[0]):
            return activated_skills[0]
        if len(registry.skills) == 1:
            return registry.skills[0].skill_id
        if activated_skills:
            raise ValueError(
                "Multiple activated skills found in this turn. Provide skill_id explicitly."
            )
        raise ValueError(
            "Skill is ambiguous for this turn. Provide skill_id explicitly."
        )

    def _load_tool_registry(
        self,
        workspace_id: str,
        *,
        persist_updates: bool = False,
    ) -> WorkspaceToolRegistry:
        registry = WorkspaceToolRegistry(self.store.get_tool_registry(workspace_id))
        changed = False
        for meta in list(registry.tools):
            hydrated = self.project_tool_runtime.hydrate_meta(meta)
            if hydrated.to_dict() != meta.to_dict():
                registry.upsert(hydrated)
                changed = True
        if changed and persist_updates:
            self.store.save_tool_registry(workspace_id, registry.to_payload())
        return registry

    def _sync_declared_skill_tools(
        self,
        workspace_id: str,
        *,
        registry: UploadedSkillRegistry | None = None,
        persist_updates: bool = False,
    ) -> WorkspaceToolRegistry:
        registry = registry or self._load_registry(workspace_id)
        tool_registry = self._load_tool_registry(workspace_id, persist_updates=persist_updates)
        desired = self._build_declared_skill_tool_metas(registry)
        changed = False

        for existing in list(tool_registry.tools):
            if existing.source.startswith("skill:") and existing.name not in desired:
                tool_registry.delete(existing.name)
                changed = True

        for tool_name, desired_meta in desired.items():
            hydrated = self.project_tool_runtime.hydrate_meta(desired_meta)
            existing = tool_registry.get(tool_name)
            if existing is None:
                tool_registry.upsert(hydrated)
                changed = True
                continue

            if existing.source.startswith("skill:"):
                if existing.to_dict() != hydrated.to_dict():
                    tool_registry.upsert(hydrated)
                    changed = True
                continue

            if existing.source == "manual":
                enriched = self._maybe_enrich_manual_tool(existing, hydrated)
                if enriched is not None and enriched.to_dict() != existing.to_dict():
                    tool_registry.upsert(enriched)
                    changed = True

        if changed and persist_updates:
            self.store.save_tool_registry(workspace_id, tool_registry.to_payload())
        return tool_registry

    @staticmethod
    def _build_declared_skill_tool_metas(registry: UploadedSkillRegistry) -> dict[str, WorkspaceToolMeta]:
        metas: dict[str, WorkspaceToolMeta] = {}
        for skill in registry.skills:
            for tool_def in skill.tool_definitions:
                if tool_def.name in metas:
                    continue
                metas[tool_def.name] = WorkspaceToolMeta(
                    name=tool_def.name,
                    description=tool_def.description,
                    execution_mode="stub",
                    enabled=True,
                    input_schema=copy.deepcopy(tool_def.input_schema),
                    source=f"skill:{skill.skill_id}",
                )
        return metas

    @staticmethod
    def _maybe_enrich_manual_tool(
        existing: WorkspaceToolMeta,
        desired: WorkspaceToolMeta,
    ) -> WorkspaceToolMeta | None:
        next_description = existing.description or desired.description
        next_schema = (
            copy.deepcopy(desired.input_schema)
            if existing.input_schema == GENERIC_OBJECT_SCHEMA
            else copy.deepcopy(existing.input_schema)
        )
        if next_description == existing.description and next_schema == existing.input_schema:
            return None
        return WorkspaceToolMeta(
            name=existing.name,
            description=next_description,
            execution_mode=existing.execution_mode,
            enabled=existing.enabled,
            input_schema=next_schema,
            source=existing.source,
        )

    def _build_system_prompt(self, mode: DebuggerMode, visible_skill_ids: list[str]) -> str:
        mode_lines = [
            "You are operating inside a product skill debugger.",
            "Uploaded skills are exposed as native project Claude skills under .claude/skills.",
            "The only Claude built-in tool available in this debugger is `Skill`.",
            "Do not ask the user to manually activate skills or load SKILL.md through a custom tool.",
            "Use Claude's native skill discovery flow to decide whether a project skill applies.",
            "Prefer answering directly when possible.",
            "Google Maps and Yelp workspace tools are live in this debugger and do hit real backends when called.",
            "If the user attached images in this turn, their image_id values will appear in the user message context. Use `recognize_image` before making visual claims.",
            "Any other workspace tools remain debug stubs: use realistic arguments, but they will not touch production backends.",
            "Tool reads, skill loads, and tool calls are recorded for debugging.",
            "If no uploaded project skill applies, answer directly and say that no uploaded skill was triggered.",
        ]
        if mode == "forced" and visible_skill_ids:
            mode_lines.extend(
                [
                    "You are in forced skill mode.",
                    f"Only one uploaded project skill is intentionally exposed for this run: `{visible_skill_ids[0]}`.",
                    "Stay within that skill's instructions if a skill is needed.",
                ]
            )
        else:
            mode_lines.extend(
                [
                    "You are in agent routing mode.",
                    "Choose naturally among the uploaded project skills visible in this workspace.",
                ]
            )
        return "\n".join(mode_lines)

    def _build_tools(
        self,
        workspace_id: str,
        tool_registry: WorkspaceToolRegistry,
        tool_runtime: StubToolRuntime,
        trace_collector: ToolTraceCollector,
    ) -> list[Any]:
        tool_defs: list[Any] = []
        for meta in tool_registry.tools:
            if not meta.enabled:
                continue
            runtime_meta = (
                self.project_tool_runtime.hydrate_meta(meta, allow_network=True)
                if meta.execution_mode.startswith("live_")
                else meta
            )
            tool_defs.append(self._make_runtime_tool(workspace_id, tool_runtime, runtime_meta, trace_collector))
        return tool_defs

    def _build_runtime_options(
        self,
        *,
        workspace_id: str,
        tool_registry: WorkspaceToolRegistry,
        tool_runtime: StubToolRuntime,
        trace_collector: ToolTraceCollector,
        mode: DebuggerMode,
        visible_skill_ids: list[str],
        runtime_project_dir: Path,
        effective_model: str | None,
        resume_session_id: str | None,
    ) -> ClaudeAgentOptions:
        tools = self._build_tools(workspace_id, tool_registry, tool_runtime, trace_collector)
        mcp_servers = {"skill_debugger": create_sdk_mcp_server("skill-debugger", tools=tools)} if tools else {}
        return ClaudeAgentOptions(
            tools=list(EXPLICIT_BUILTIN_TOOLS),
            allowed_tools=[*EXPLICIT_BUILTIN_TOOLS, *[_sdk_workspace_tool_name(tool_def.name) for tool_def in tools]],
            mcp_servers=mcp_servers,
            system_prompt=self._build_system_prompt(mode, visible_skill_ids),
            cwd=str(runtime_project_dir),
            model=effective_model,
            max_turns=10,
            include_partial_messages=True,
            resume=resume_session_id,
            setting_sources=["project"],
            env=self.settings.runtime_env(),
            permission_mode="bypassPermissions",
        )

    def _prepare_runtime_project(
        self,
        *,
        workspace_id: str,
        registry: UploadedSkillRegistry,
        mode: DebuggerMode,
        visible_skill_ids: list[str],
    ) -> Path:
        canonical_workspace = self.store.workspace_dir(workspace_id)
        if mode != "forced":
            return canonical_workspace

        runtime_key = f"forced-{visible_skill_ids[0]}"
        runtime_project = self.store.runtime_project_dir(workspace_id, runtime_key)
        if runtime_project.exists():
            shutil.rmtree(runtime_project)
        runtime_skills_dir = runtime_project / ".claude" / "skills"
        runtime_skills_dir.mkdir(parents=True, exist_ok=True)

        for skill_id in visible_skill_ids:
            meta = registry.get_skill_meta(skill_id)
            if not meta or not meta.source_path:
                raise ValueError(f"Skill source is unavailable for forced mode: {skill_id}")
            source_dir = Path(meta.source_path).parent
            shutil.copytree(source_dir, runtime_skills_dir / source_dir.name)

        return runtime_project

    def _reset_runtime_projects(self, workspace_id: str) -> None:
        runtime_root = self.store.runtime_projects_dir(workspace_id)
        if runtime_root.exists():
            shutil.rmtree(runtime_root)
        runtime_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _serialize_workspace_tools(
        registry: UploadedSkillRegistry,
        tool_registry: WorkspaceToolRegistry,
    ) -> list[dict[str, Any]]:
        declared_by_tool: dict[str, list[str]] = {}
        for skill in registry.skills:
            for tool_name in skill.declared_tools:
                declared_by_tool.setdefault(tool_name, [])
                if skill.skill_id not in declared_by_tool[tool_name]:
                    declared_by_tool[tool_name].append(skill.skill_id)

        items: list[dict[str, Any]] = []
        for tool_meta in tool_registry.tools:
            payload = tool_meta.to_dict()
            payload["declared_by_skills"] = declared_by_tool.get(tool_meta.name, [])
            items.append(payload)
        return items

    @staticmethod
    def _collect_unregistered_declared_tools(
        registry: UploadedSkillRegistry,
        tool_registry: WorkspaceToolRegistry,
    ) -> list[dict[str, Any]]:
        declared_by_tool: dict[str, list[str]] = {}
        for skill in registry.skills:
            for tool_name in skill.declared_tools:
                declared_by_tool.setdefault(tool_name, [])
                if skill.skill_id not in declared_by_tool[tool_name]:
                    declared_by_tool[tool_name].append(skill.skill_id)

        return [
            {"name": tool_name, "declared_by_skills": skills}
            for tool_name, skills in sorted(declared_by_tool.items())
            if not tool_registry.has_tool(tool_name)
        ]

    @classmethod
    def _build_skill_packages(cls, files: list[tuple[str, bytes]]) -> list[SkillPackageUpload]:
        expanded = cls._expand_uploaded_entries(files)
        standalone_packages: list[SkillPackageUpload] = []
        asset_files: list[tuple[str, bytes]] = []

        for rel_path, raw in expanded:
            path = PurePosixPath(rel_path)
            if path.name.lower().endswith(".md") and path.name != "SKILL.md" and str(path.parent) == ".":
                standalone_packages.append(
                    SkillPackageUpload(
                        package_name=path.stem or "skill",
                        files={"SKILL.md": raw},
                        source_kind="single_file",
                    )
                )
                continue
            asset_files.append((rel_path, raw))

        skill_roots = sorted(
            {
                str(PurePosixPath(rel_path).parent)
                for rel_path, _ in asset_files
                if PurePosixPath(rel_path).name == "SKILL.md"
            }
        )

        grouped_packages: list[SkillPackageUpload] = []
        for skill_root in skill_roots:
            prefix = "" if skill_root in {"", "."} else skill_root.strip("/") + "/"
            files_in_package: dict[str, bytes] = {}
            for rel_path, raw in asset_files:
                if prefix and not rel_path.startswith(prefix):
                    continue
                local_path = rel_path[len(prefix) :] if prefix else rel_path
                if local_path.startswith("/"):
                    local_path = local_path[1:]
                if not local_path:
                    continue
                files_in_package[local_path] = raw
            package_name = PurePosixPath(skill_root).name if skill_root not in {"", "."} else "skill"
            if files_in_package:
                grouped_packages.append(
                    SkillPackageUpload(
                        package_name=package_name,
                        files=files_in_package,
                        source_kind="folder" if skill_root not in {"", "."} else "single_file",
                    )
                )

        packages = standalone_packages + grouped_packages
        if packages:
            return packages

        skill_md_candidates = sorted(
            rel_path
            for rel_path, _ in expanded
            if PurePosixPath(rel_path).name.lower() == "skill.md"
        )
        if skill_md_candidates:
            raise ValueError(
                "Found skill file candidates, but the main skill file must be named exactly `SKILL.md`: "
                + ", ".join(skill_md_candidates)
            )
        return packages

    @classmethod
    def _expand_uploaded_entries(cls, files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
        expanded: list[tuple[str, bytes]] = []
        for rel_path, raw in files:
            normalized = cls._normalize_upload_path(rel_path)
            if not normalized:
                continue
            if normalized.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        child_path = cls._normalize_upload_path(info.filename)
                        if not child_path or child_path.startswith("__MACOSX/"):
                            continue
                        expanded.append((child_path, archive.read(info)))
                continue
            expanded.append((normalized, raw))
        return expanded

    @staticmethod
    def _normalize_upload_path(path: str | None) -> str:
        raw = str(path or "").replace("\\", "/").strip()
        if not raw:
            return ""
        parts = [part for part in raw.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise ValueError(f"Invalid upload path: {path}")
        return "/".join(parts)

    @staticmethod
    def _can_resume_session(
        session: dict[str, Any],
        mode: DebuggerMode,
        forced_skill_id: str | None,
        model: str | None,
        runtime_cwd: str,
    ) -> bool:
        if not session.get("claude_session_id"):
            return False
        if session.get("last_mode") != mode:
            return False
        if session.get("last_forced_skill_id") != forced_skill_id:
            return False
        if session.get("last_model") != model:
            return False
        if session.get("last_runtime_cwd") != runtime_cwd:
            return False
        return True

    def _load_attached_images(self, workspace_id: str, image_ids: list[str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in image_ids:
            image_id = str(raw or "").strip()
            if not image_id or image_id in seen:
                continue
            seen.add(image_id)
            payload = self.store.get_uploaded_image(workspace_id, image_id)
            items.append(
                {
                    "image_id": payload["image_id"],
                    "filename": payload["filename"],
                    "mime_type": payload["mime_type"],
                    "size_bytes": payload["size_bytes"],
                    "url": payload["url"],
                }
            )
        return items

    @staticmethod
    def _build_prompt_with_attached_images(prompt: str, attached_images: list[dict[str, Any]]) -> str:
        if not attached_images:
            return prompt
        lines = [prompt] if prompt else ["The user uploaded image attachments without additional text."]
        lines.extend(["", "Attached images for this turn:"])
        for index, image in enumerate(attached_images, start=1):
            lines.append(
                f"{index}. image_id={image['image_id']} · filename={image['filename']} · mime_type={image['mime_type']}"
            )
        lines.append(
            "If visual understanding is required, call `recognize_image` with the relevant `image_id`. Do not claim image details unless you actually use that tool."
        )
        return "\n".join(lines)

    def _prepare_runtime_tool_args(
        self,
        workspace_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        runtime_args = dict(args)
        if tool_name != "recognize_image":
            return runtime_args
        has_explicit_source = any(
            str(runtime_args.get(field) or "").strip()
            for field in ("image_url", "image_base64", "image_path")
        )
        image_id = str(runtime_args.get("image_id") or "").strip()
        if has_explicit_source or not image_id:
            return runtime_args
        image_meta = self.store.get_uploaded_image(workspace_id, image_id)
        image_path = self.store.get_uploaded_image_path(workspace_id, image_id)
        runtime_args["image_path"] = str(image_path)
        runtime_args.setdefault("mime_type", image_meta.get("mime_type"))
        return runtime_args

    def _make_runtime_tool(
        self,
        workspace_id: str,
        tool_runtime: StubToolRuntime,
        tool_meta: WorkspaceToolMeta,
        trace_collector: ToolTraceCollector,
    ):
        @tool(
            tool_meta.name,
            tool_meta.description
            or f"Debug stub for {tool_meta.name}. Records realistic arguments without hitting production backends.",
            copy.deepcopy(tool_meta.input_schema or GENERIC_OBJECT_SCHEMA),
        )
        async def runtime_tool(args: dict[str, Any]) -> dict[str, Any]:
            parsed_output: Any
            status = "ok"
            original_args = dict(args)
            if tool_meta.execution_mode.startswith("live_"):
                try:
                    runtime_args = self._prepare_runtime_tool_args(workspace_id, tool_meta.name, original_args)
                    text = await self.project_tool_runtime.execute(tool_meta.name, runtime_args)
                    parsed_output = _maybe_parse_json_text(text)
                except Exception as exc:
                    status = "error"
                    text = json.dumps(
                        {
                            "status": "error",
                            "tool": tool_meta.name,
                            "message": f"Live tool execution failed: {exc}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    parsed_output = _maybe_parse_json_text(text)
            else:
                text = tool_runtime.invoke_stub_tool(tool_meta.name, dict(args))
                parsed_output = _maybe_parse_json_text(text)

            trace_collector.record_local_tool_execution(
                tool_name=_sdk_workspace_tool_name(tool_meta.name),
                tool_input=original_args,
                output=parsed_output,
                status=status,
            )
            return {"content": [{"type": "text", "text": text}]}

        return runtime_tool

    @staticmethod
    def _format_upload_lint_errors(reports: list[SkillLintReport]) -> str:
        lines = ["Upload linter failed. Fix the following issues before uploading:"]
        for report in reports:
            label = report.skill_name or report.package_name or "skill"
            lines.append(f"- {label}")
            for finding in report.errors:
                detail = finding.message
                if finding.path:
                    detail = f"{detail} [{finding.path}]"
                elif finding.field:
                    detail = f"{detail} [{finding.field}]"
                lines.append(f"  - {detail}")
        return "\n".join(lines)
