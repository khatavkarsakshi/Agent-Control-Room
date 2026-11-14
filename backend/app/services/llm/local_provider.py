"""Local / self-hosted model adapter.

Ollama, vLLM, LM Studio and llama.cpp all expose an OpenAI-compatible
``/v1/chat/completions`` endpoint, so the OpenAI adapter is reused with a
different base URL and a placeholder API key.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.llm.openai_provider import OpenAIProvider


class LocalProvider(OpenAIProvider):
    name = "local"

    def __init__(self) -> None:
        super().__init__(
            api_key=settings.local_llm_api_key or "local",
            base_url=settings.local_llm_base_url,
            provider_name="local",
        )
