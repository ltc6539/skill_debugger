from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from claude_agent_sdk._errors import ProcessError
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from skill_debugger.service import SkillDebuggerService
from skill_debugger.settings import SkillDebuggerSettings
from skill_debugger.store import WorkspaceStore
from skill_debugger.tool_registry import WorkspaceToolMeta


class IdentityProjectToolRuntime:
    def hydrate_meta(self, meta: WorkspaceToolMeta, *, allow_network: bool = False) -> WorkspaceToolMeta:
        return meta


class RetryOnceRuntime:
    def __init__(self) -> None:
        self.calls = 0
        self.options_history = []

    async def stream(self, prompt: str, options):  # type: ignore[override]
        self.calls += 1
        self.options_history.append(options)
        if self.calls == 1:
            raise ProcessError(message="session expired", exit_code=1, stderr="session expired")

        server = options.mcp_servers["skill_debugger"]
        runtime_tool = server["tools"][0]
        tool_input = {"query": "hello"}
        await runtime_tool.handler(tool_input)

        yield AssistantMessage(
            content=[
                ToolUseBlock(id="tool_use_1", name=f"mcp__skill_debugger__{runtime_tool.name}", input=tool_input),
                TextBlock(text="handled"),
            ],
            model="test-model",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="fresh-session",
            stop_reason="end_turn",
            result="handled",
        )


class SkillDebuggerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = WorkspaceStore(Path(self.tempdir.name) / "workspaces")
        self.workspace = self.store.create_workspace("retry")
        self.settings = SkillDebuggerSettings(
            env_file=Path(self.tempdir.name) / ".env",
            openrouter_api_key=None,
            openrouter_base_url="https://openrouter.ai/api",
            default_model="test-model",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_run_chat_rebuilds_tool_trace_state_after_retry(self) -> None:
        runtime = RetryOnceRuntime()
        service = SkillDebuggerService(
            store=self.store,
            settings=self.settings,
            runtime=runtime,
            project_tool_runtime=IdentityProjectToolRuntime(),
        )
        workspace_id = self.workspace["workspace_id"]
        self.store.save_tool_registry(
            workspace_id,
            {
                "tools": [
                    WorkspaceToolMeta(name="debug_lookup", description="Debug lookup", execution_mode="stub").to_dict()
                ]
            },
        )
        session = self.store.get_session(workspace_id)
        session["claude_session_id"] = "stale-session"
        session["last_mode"] = "agent"
        session["last_forced_skill_id"] = None
        session["last_model"] = "test-model"
        session["last_runtime_cwd"] = str(self.store.workspace_dir(workspace_id).resolve())
        self.store.save_session(workspace_id, session)

        async def collect_events() -> list[dict]:
            items: list[dict] = []
            async for event in service.run_chat(
                workspace_id=workspace_id,
                message="use the debug tool",
                mode="agent",
            ):
                items.append(event)
            return items

        with mock.patch(
            "skill_debugger.service.create_sdk_mcp_server",
            side_effect=lambda name, tools: {"type": "sdk", "name": name, "tools": tools},
        ):
            events = asyncio.run(collect_events())

        self.assertEqual(runtime.calls, 2)
        self.assertFalse(any(event["event"] == "error" for event in events))
        self.assertIn("mcp__skill_debugger__debug_lookup", runtime.options_history[-1].allowed_tools)
        self.assertNotIn("debug_lookup", runtime.options_history[-1].allowed_tools)

        done_event = next(event for event in events if event["event"] == "done")
        self.assertEqual(done_event["data"]["assistant_message"], "handled")
        self.assertEqual(done_event["data"]["session"]["claude_session_id"], "fresh-session")

        trace = done_event["data"]["trace"]
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["tool"], "mcp__skill_debugger__debug_lookup")
        self.assertEqual(trace[0]["status"], "ok")
        self.assertEqual(trace[0]["output"]["status"], "stubbed")
        self.assertEqual(trace[0]["output"]["tool"], "debug_lookup")


if __name__ == "__main__":
    unittest.main()
