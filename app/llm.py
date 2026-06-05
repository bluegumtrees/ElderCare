from typing import AsyncIterator

from openai import AsyncOpenAI

from .config import get_settings

_client: AsyncOpenAI | None = None


def get_llm() -> AsyncOpenAI:
    global _client
    if _client is None:
        s = get_settings()
        _client = AsyncOpenAI(
            api_key=s.openrouter_api_key,
            base_url=s.openrouter_base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/eldercare-rag",
                "X-Title": "ElderCare RAG",
            },
        )
    return _client


async def stream_chat(messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
    s = get_settings()
    client = get_llm()
    stream = await client.chat.completions.create(
        model=model or s.chat_model,
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
