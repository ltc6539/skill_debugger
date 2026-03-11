from __future__ import annotations

import dataclasses
import logging
from collections.abc import AsyncIterator

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk._errors import ProcessError
from claude_agent_sdk.types import Message

logger = logging.getLogger(__name__)


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
                async for message in client.receive_response():
                    yield message
        except ProcessError as exc:
            captured = "\n".join(stderr_lines) if stderr_lines else None
            raise ProcessError(
                message=captured or str(exc),
                exit_code=exc.exit_code,
                stderr=captured or exc.stderr,
            ) from exc
