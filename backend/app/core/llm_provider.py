from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import settings


class BaseLLMProvider(ABC):
    @abstractmethod
    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        pass

    @abstractmethod
    async def generate_embedding(self, text: str) -> List[float]:
        pass


class MockLLMProvider(BaseLLMProvider):
    """
    Deterministic mock provider for unit testing and offline execution.
    Ensures complete system runs locally without requiring an active OpenAI key.
    """
    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        last_msg = messages[-1]["content"].lower() if messages else ""
        if "charge" in last_msg or "duplicate" in last_msg or "order" in last_msg:
            return "I understand your issue regarding order charges. Let me assist you or connect you with our billing specialists."
        if "compromise" in last_msg or "hacked" in last_msg:
            return "We take security very seriously. Your account security inquiry is being prioritized."
        return "Thank you for contacting customer support. How can I assist you with your order or policy inquiries today?"

    async def generate_embedding(self, text: str) -> List[float]:
        # Return standard 1536-dimensional mock unit vector
        vec = [0.0] * 1536
        if text:
            # Deterministic variation based on text length
            val = (len(text) % 100) / 100.0
            vec[0] = val
            vec[1] = 1.0 - val
        return vec


class OpenAILLMProvider(BaseLLMProvider):
    """Production provider connecting directly to OpenAI API."""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.model = settings.OPENAI_MODEL
        self.embedding_model = settings.OPENAI_EMBEDDING_MODEL

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": temperature
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def generate_embedding(self, text: str) -> List[float]:
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.embedding_model,
            "input": text
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]


def get_llm_provider() -> BaseLLMProvider:
    if settings.AI_PROVIDER == "openai" and settings.OPENAI_API_KEY:
        return OpenAILLMProvider(settings.OPENAI_API_KEY)
    return MockLLMProvider()
