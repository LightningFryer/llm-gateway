"""LLM Provider interfaces and completion schemas."""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single chat message in the conversation history."""

    role: str
    content: str
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request schema."""

    model: str
    messages: list[ChatMessage]
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    stop: str | list[str] | None = None
    stream: bool = False
    user: str | None = None


class TokenUsage(BaseModel):
    """Token accounting report."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionChoice(BaseModel):
    """A single choice in a non-streaming response."""

    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible non-streaming response."""

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChatCompletionChoice]
    usage: TokenUsage = Field(default_factory=TokenUsage)


class DeltaMessage(BaseModel):
    """Delta update contained in an SSE streaming chunk."""

    role: str | None = None
    content: str | None = None


class StreamingChoice(BaseModel):
    """Choice representation in an SSE stream chunk."""

    index: int = 0
    delta: DeltaMessage
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    """OpenAI-compatible streaming chunk."""

    id: str
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[StreamingChoice]
    usage: TokenUsage | None = None


@dataclass
class ProviderResponse:
    """Wrapper holding execution metadata alongside completion result."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    ttft_ms: float | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


class Provider(ABC):
    """Abstract base class that all provider adapters (Mock, OpenAI, Anthropic, Gemini) implement."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def complete(
        self,
        request: ChatCompletionRequest,
        upstream_model: str,
        timeout_seconds: float = 30.0,
    ) -> ProviderResponse:
        """Executes a non-streaming chat completion."""
        ...

    @abstractmethod
    def stream(
        self,
        request: ChatCompletionRequest,
        upstream_model: str,
        timeout_seconds: float = 30.0,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Yields streaming SSE chunks token-by-token."""
        ...
