from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk._errors import ProcessError
from claude_agent_sdk.types import Message, ResultMessage

logger = logging.getLogger(__name__)

_PROCESS_EXIT_TIMEOUT_SECONDS = 0.75
_SESSION_SETTLE_TIMEOUT_SECONDS = 1.0
_SESSION_SETTLE_POLL_SECONDS = 0.05
_SESSION_SETTLE_STABLE_POLLS = 3
_NON_ALNUM_PATH_CHARS = re.compile(r"[^A-Za-z0-9]+")


def _claude_home_dir(runtime_cwd: str) -> Path:
    resolved = Path(runtime_cwd).resolve()
    parts = resolved.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "home":
        return Path("/") / "home" / parts[2]
    if len(parts) >= 2 and parts[0] == "/" and parts[1] == "root":
        return Path("/root")
    return Path.home()


def _claude_session_log_path(runtime_cwd: str, session_id: str) -> Path | None:
    cwd = str(runtime_cwd or "").strip()
    session = str(session_id or "").strip()
    if not cwd or not session:
        return None

    resolved_cwd = str(Path(cwd).resolve())
    project_slug = _NON_ALNUM_PATH_CHARS.sub("-", resolved_cwd).strip("-")
    if not project_slug:
        return None

    return _claude_home_dir(cwd) / ".claude" / "projects" / f"-{project_slug}" / f"{session}.jsonl"


async def _wait_for_session_log_settle(runtime_cwd: str, session_id: str) -> None:
    log_path = _claude_session_log_path(runtime_cwd, session_id)
    if log_path is None or not log_path.parent.exists():
        return

    deadline = asyncio.get_running_loop().time() + _SESSION_SETTLE_TIMEOUT_SECONDS
    last_signature: tuple[int, int] | None = None
    stable_polls = 0

    while True:
        signature: tuple[int, int] | None = None
        with suppress(OSError):
            if log_path.exists():
                stat = log_path.stat()
                signature = (stat.st_size, stat.st_mtime_ns)

        if signature is not None and signature == last_signature:
            stable_polls += 1
        else:
            stable_polls = 0
            last_signature = signature

        if signature is not None and stable_polls >= _SESSION_SETTLE_STABLE_POLLS:
            return
        if asyncio.get_running_loop().time() >= deadline:
            logger.debug("Claude session log did not fully settle before timeout: %s", log_path)
            return

        await asyncio.sleep(_SESSION_SETTLE_POLL_SECONDS)


class ClaudeSdkRuntime:
    async def stream(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        stderr_lines: list[str] = []

        def _capture_stderr(line: str) -> None:
            stripped = line.rstrip()
            logger.error("claude stderr: %s", stripped)
            stderr_lines.append(stripped)

        # Avoid mutating caller's options
        opts = dataclasses.replace(options, stderr=_capture_stderr)

        try:
            async with ClaudeSDKClient(options=opts) as client:
                await client.query(prompt)
                async for message in client.receive_messages():
                    yield message
                    if isinstance(message, ResultMessage):
                        await self._finalize_streaming_session(
                            client,
                            runtime_cwd=str(options.cwd or "").strip(),
                            session_id=message.session_id,
                        )
                        return
        except ProcessError as exc:
            captured = "\n".join(stderr_lines) if stderr_lines else None
            raise ProcessError(
                message=captured or str(exc),
                exit_code=exc.exit_code,
                stderr=captured or exc.stderr,
            ) from exc

    async def _finalize_streaming_session(
        self,
        client: ClaudeSDKClient,
        *,
        runtime_cwd: str,
        session_id: str | None,
    ) -> None:
        transport = getattr(client, "_transport", None)
        if transport is not None:
            with suppress(Exception):
                await transport.end_input()

            process = getattr(transport, "_process", None)
            if process is not None and getattr(process, "returncode", None) is None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=_PROCESS_EXIT_TIMEOUT_SECONDS)

        if runtime_cwd and session_id:
            await _wait_for_session_log_settle(runtime_cwd, session_id)
