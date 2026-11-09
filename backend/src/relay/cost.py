"""Token estimation using tiktoken, cost calculation, and monthly budget enforcement."""

from __future__ import annotations

import logging
from typing import Any

import tiktoken

from relay.db import get_monthly_spend

logger = logging.getLogger("relay.cost")

# Cache tiktoken encoding instance
_encoding: Any = None


def get_token_encoding() -> Any:
    """Returns or initializes the tiktoken encoding instance."""
    global _encoding
    if _encoding is None:
        try:
            _encoding = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.warning(
                "Failed to initialize cl100k_base tiktoken encoding (%s). Using fallback estimator.",
                e,
            )
            _encoding = False
    return _encoding


def estimate_tokens(text: str) -> int:
    """Estimates or counts token count for a text string."""
    if not text:
        return 0
    enc = get_token_encoding()
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Reliable character/word fallback estimation
    words = text.split()
    return max(1, int(len(words) * 1.3))


def estimate_messages_tokens(messages: list[dict[str, Any]] | list[Any]) -> int:
    """Estimates total prompt tokens across a list of chat messages."""
    total = 3  # Formatting overhead
    for msg in messages:
        total += 4  # Per-message overhead (role, markers)
        content = getattr(msg, "content", "") if hasattr(msg, "content") else msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
    return total


def calculate_cost_usd(
    tokens_in: int,
    tokens_out: int,
    cost_in_per_m: float,
    cost_out_per_m: float,
) -> float:
    """Calculates cost in USD based on input/output tokens and per-million token rates."""
    return (tokens_in / 1_000_000.0) * cost_in_per_m + (tokens_out / 1_000_000.0) * cost_out_per_m


async def check_budget_available(
    db_path: str,
    key_id: str,
    monthly_budget_usd: float | None,
) -> tuple[bool, float]:
    """Checks whether this API key has remaining budget for the current month.

    Returns (is_allowed, current_spend_usd). A budget of None or <= 0 is treated as unlimited.
    """
    current_spend = await get_monthly_spend(db_path, key_id)
    if monthly_budget_usd is None or monthly_budget_usd <= 0.0:
        return True, current_spend
    if current_spend >= monthly_budget_usd:
        return False, current_spend
    return True, current_spend
