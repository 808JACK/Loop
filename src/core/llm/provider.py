"""
LLM Provider Factory — Claude / Gemini / Groq / Ollama.

See HLD §7.5 for specification.
"""

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from groq import Groq
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.tool import ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

# Re-export throttle helpers so nodes only need one import
from src.core.llm.throttler import (  # noqa: F401
    RateLimitError,
    SessionLimitError,
    call_llm,
    get_throttler,
)
from src.settings import settings
from src.utils import normalize_key

logger = logging.getLogger("llm_provider")


class LLMProviderError(Exception):
    """Exception raised for errors in the LLM provider configuration or execution."""

    pass


_OLLAMA_KEY_LOCK = threading.Lock()
_OLLAMA_KEY_INDEX = 0
_OLLAMA_EXHAUSTED_KEYS: set[str] = set()
_LAST_OLLAMA_KEY: str | None = None


class GroqSDKChatModel(BaseChatModel):
    """Minimal Groq SDK-backed chat model that stays LangChain-compatible."""

    model_name: str
    groq_api_key: str
    temperature: float = 0.7
    max_tokens: int | None = None

    @property
    def _llm_type(self) -> str:
        return "groq-sdk"

    @property
    def _identifying_params(self) -> dict:
        return {
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    def bind_tools(
        self,
        tools,
        *,
        tool_choice: str | None = None,
        **kwargs,
    ):
        """Bind tools in OpenAI-compatible schema for Groq function calling."""
        converted_tools = [convert_to_openai_tool(tool) for tool in tools]
        bind_kwargs = {"tools": converted_tools, **kwargs}
        if tool_choice is not None:
            bind_kwargs["tool_choice"] = tool_choice
        return self.bind(**bind_kwargs)

    @staticmethod
    def _to_groq_message(message: BaseMessage) -> dict:
        if isinstance(message, SystemMessage):
            return {"role": "system", "content": message.content}
        if isinstance(message, HumanMessage):
            return {"role": "user", "content": message.content}
        if isinstance(message, ToolMessage):
            return {
                "role": "tool",
                "content": message.content,
                "tool_call_id": message.tool_call_id,
            }
        if isinstance(message, AIMessage):
            payload: dict = {"role": "assistant", "content": message.content or ""}
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                payload["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"]),
                        },
                    }
                    for tc in tool_calls
                ]
            return payload
        return {"role": "user", "content": getattr(message, "content", str(message))}

    @staticmethod
    def _to_ai_message(choice_message) -> AIMessage:
        tool_calls: list[ToolCall] = []
        for tool_call in getattr(choice_message, "tool_calls", None) or []:
            arguments = tool_call.function.arguments or "{}"
            try:
                parsed_args = json.loads(arguments)
            except Exception:
                parsed_args = {"__raw__": arguments}
            tool_calls.append(
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    args=parsed_args,
                )
            )

        return AIMessage(content=choice_message.content or "", tool_calls=tool_calls)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        client = Groq(api_key=self.groq_api_key)

        payload = {
            "model": self.model_name,
            "messages": [self._to_groq_message(message) for message in messages],
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens

        extra_kwargs = dict(kwargs)
        tools = extra_kwargs.pop("tools", None)
        tool_choice = extra_kwargs.pop("tool_choice", None)
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if stop:
            payload["stop"] = stop
        payload.update(extra_kwargs)

        class _GroqRequest:
            def invoke(self, _inputs):
                return client.chat.completions.create(**payload)

        max_tool_retries = 1
        for attempt in range(max_tool_retries + 1):
            try:
                response = get_throttler().invoke(_GroqRequest(), {})
                choice = response.choices[0]
                generation = ChatGeneration(message=self._to_ai_message(choice.message))
                return ChatResult(
                    generations=[generation],
                    llm_output={
                        "model": self.model_name,
                        "usage": getattr(response, "usage", None),
                    },
                )
            except Exception as e:
                error_str = str(e).lower()
                if (
                    "tool_use_failed" in error_str or "failed to call a function" in error_str
                ) and attempt < max_tool_retries:
                    logger.warning(f"[groq] tool call generation failed, retrying once: {e}")
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        raise RuntimeError("Groq _generate exhausted retries without returning")


def _split_key_list(value: str) -> list[str]:
    parts = re.split(r"[,\n;]+", value)
    return [normalize_key(part) for part in parts if normalize_key(part)]


def _read_ollama_keys_from_env_files() -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    repo_root = Path(__file__).resolve().parents[4]
    candidate_files = [repo_root / ".env", repo_root.parent / ".env"]

    for env_path in candidate_files:
        if not env_path.exists():
            continue

        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"^\s*#?\s*OLLAMA_API_KEY\s*=\s*(.+?)\s*$", raw_line)
                if not match:
                    continue
                key = normalize_key(match.group(1))
                if key and key not in seen:
                    seen.add(key)
                    keys.append(key)
        except Exception:  # nosec B112
            continue

    return keys


def _get_ollama_key_pool() -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    for key in _split_key_list(os.getenv("OLLAMA_API_KEYS", "")):
        if key not in seen:
            seen.add(key)
            keys.append(key)

    if settings.ollama_api_key and settings.ollama_api_key not in seen:
        seen.add(settings.ollama_api_key)
        keys.append(settings.ollama_api_key)

    for key in _read_ollama_keys_from_env_files():
        if key not in seen:
            seen.add(key)
            keys.append(key)

    return keys


def _select_ollama_api_key() -> tuple[str | None, int, int]:
    global _OLLAMA_KEY_INDEX, _LAST_OLLAMA_KEY

    with _OLLAMA_KEY_LOCK:
        pool = [key for key in _get_ollama_key_pool() if key not in _OLLAMA_EXHAUSTED_KEYS]
        if not pool:
            pool = _get_ollama_key_pool()
            _OLLAMA_EXHAUSTED_KEYS.clear()

        if not pool:
            _LAST_OLLAMA_KEY = None
            return None, 0, 0

        slot = _OLLAMA_KEY_INDEX % len(pool)
        key = pool[slot]
        _OLLAMA_KEY_INDEX = (_OLLAMA_KEY_INDEX + 1) % len(pool)
        _LAST_OLLAMA_KEY = key
        return key, slot + 1, len(pool)


def mark_ollama_api_key_exhausted(api_key: str | None = None) -> None:
    """Remember that the current Ollama key hit a usage limit so the next call can rotate."""
    global _LAST_OLLAMA_KEY

    key = api_key or _LAST_OLLAMA_KEY
    if not key:
        return

    with _OLLAMA_KEY_LOCK:
        _OLLAMA_EXHAUSTED_KEYS.add(key)
        logger.warning(
            "[llm] Marked current Ollama API key as exhausted; will rotate to the next key"
        )


def get_chat_model(
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """
    Return a LangChain chat model for the configured provider.

    Falls back to settings values when provider/model are not supplied.
    """
    provider = provider or settings.llm_provider
    model = model or settings.llm_model

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        api_key, slot, pool_size = _select_ollama_api_key()
        
        # For Ollama Cloud, use the base URL
        base_url = settings.ollama_base_url
        if "api.ollama.com" in base_url:
            # Ollama Cloud base URL
            base_url = "https://api.ollama.com"
        
        kwargs: dict = {
            "model": model,
            "base_url": base_url,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["num_predict"] = max_tokens
        if api_key:
            logger.info(f"[llm] Using Ollama API key slot {slot}/{pool_size}")
            logger.info(f"[llm] Ollama model: {model}, base_url: {base_url}")
            # Pass API key via headers for Ollama Cloud
            auth_headers = {"Authorization": f"Bearer {api_key}"}
            kwargs["client_kwargs"] = {"headers": auth_headers}
            kwargs["async_client_kwargs"] = {"headers": auth_headers}
        return ChatOllama(**kwargs)

    if provider == "claude":
        from langchain_anthropic import ChatAnthropic

        if not settings.anthropic_api_key:
            raise LLMProviderError("ANTHROPIC_API_KEY is required for provider=claude")
        kwargs = {
            "model": model,
            "api_key": settings.anthropic_api_key,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        return ChatAnthropic(**kwargs)  # type: ignore[no-any-return]

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not settings.google_api_key:
            raise LLMProviderError("GOOGLE_API_KEY is required for provider=gemini")
        kwargs = {
            "model": model,
            "google_api_key": settings.google_api_key,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_output_tokens"] = max_tokens
        return ChatGoogleGenerativeAI(**kwargs)  # type: ignore[no-any-return]

    if provider == "groq":
        if not settings.groq_api_key:
            raise LLMProviderError("GROQ_API_KEY is required for provider=groq")
        return GroqSDKChatModel(
            model_name=model,
            groq_api_key=settings.groq_api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    raise LLMProviderError(
        f"Unknown LLM_PROVIDER: '{provider}'. Valid values: claude, gemini, groq, ollama"
    )


def get_chat_model_for_node(
    node_name: str,
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Get a chat model for a specific graph node (all nodes share one provider for MVP)."""
    return get_chat_model(temperature=temperature, max_tokens=max_tokens)
