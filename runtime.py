from __future__ import annotations

from collections.abc import AsyncIterator

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import Message


class ClaudeSdkRuntime:
    async def stream(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                yield message
