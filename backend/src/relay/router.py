"""Model alias configuration and routing strategy implementation.

Maps high-level user aliases (e.g. 'fast', 'smart', 'cheap') to target providers
and upstream models according to priority, round-robin, lowest-latency, or lowest-cost rules.
"""

from __future__ import annotations

import itertools
import logging
import os
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger("relay.router")

RoutingStrategy = Literal["priority", "round_robin", "lowest_latency", "lowest_cost"]


class TargetConfig(BaseModel):
    """An individual upstream provider target."""

    provider: str
    model: str
    weight: int = 1
    cost_input_per_million: float = 0.0
    cost_output_per_million: float = 0.0


class AliasConfig(BaseModel):
    """Configuration for a model alias."""

    strategy: RoutingStrategy = "priority"
    description: str = ""
    targets: list[TargetConfig] = Field(default_factory=list)


class Router:
    """Manages model alias definitions and selects candidate targets based on strategies."""

    def __init__(self, config_path: str = "config/models.yaml") -> None:
        self.config_path = config_path
        self.aliases: dict[str, AliasConfig] = {}
        # Stateful counters for round-robin strategy per alias
        self._round_robin_iterators: dict[str, Any] = {}
        self.load_config()

    def load_config(self) -> None:
        """Loads or reloads alias mappings from YAML file."""
        if not os.path.exists(self.config_path):
            logger.warning(
                "Config file not found at %s. Using default fallback aliases.", self.config_path
            )
            self._set_default_fallback_config()
            return

        try:
            with open(self.config_path, encoding="utf-8") as f:
                raw_data = yaml.safe_load(f) or {}

            raw_aliases = raw_data.get("aliases", {})
            new_aliases: dict[str, AliasConfig] = {}
            for name, cfg in raw_aliases.items():
                new_aliases[name] = AliasConfig(**cfg)

            self.aliases = new_aliases
            self._round_robin_iterators.clear()
            logger.info("Loaded %d model aliases from %s", len(self.aliases), self.config_path)
        except Exception as e:
            logger.error("Failed to load router config from %s: %s", self.config_path, e)
            if not self.aliases:
                self._set_default_fallback_config()

    def _set_default_fallback_config(self) -> None:
        """Sets hardcoded mock aliases when config file is missing."""
        self.aliases = {
            "fast": AliasConfig(
                strategy="priority",
                description="Fast fallback mock targets",
                targets=[
                    TargetConfig(
                        provider="mock-a",
                        model="mock-gpt-4o-mini",
                        weight=1,
                        cost_input_per_million=0.15,
                        cost_output_per_million=0.60,
                    ),
                    TargetConfig(
                        provider="mock-b",
                        model="mock-claude-3-haiku",
                        weight=1,
                        cost_input_per_million=0.25,
                        cost_output_per_million=1.25,
                    ),
                ],
            ),
            "smart": AliasConfig(
                strategy="priority",
                description="Flagship reasoning targets",
                targets=[
                    TargetConfig(
                        provider="mock-a",
                        model="mock-gpt-4o",
                        weight=1,
                        cost_input_per_million=2.50,
                        cost_output_per_million=10.00,
                    ),
                    TargetConfig(
                        provider="mock-b",
                        model="mock-claude-3-5-sonnet",
                        weight=1,
                        cost_input_per_million=3.00,
                        cost_output_per_million=15.00,
                    ),
                ],
            ),
        }

    def get_alias(self, alias_name: str) -> AliasConfig | None:
        """Retrieves configuration for a given alias."""
        return self.aliases.get(alias_name)

    def list_models(self) -> list[dict[str, Any]]:
        """Returns list of configured model aliases in OpenAI model listing format."""
        return [
            {
                "id": alias_name,
                "object": "model",
                "created": 1700000000,
                "owned_by": "relay",
                "description": cfg.description,
                "strategy": cfg.strategy,
                "targets_count": len(cfg.targets),
            }
            for alias_name, cfg in self.aliases.items()
        ]

    def plan_targets(
        self,
        alias_name: str,
        open_circuit_providers: set[str] | None = None,
        provider_latencies: dict[str, float] | None = None,
    ) -> list[TargetConfig]:
        """Plans ordered sequence of target candidates according to alias strategy.

        Targets whose provider has an OPEN circuit are skipped.
        """
        cfg = self.get_alias(alias_name)
        if not cfg or not cfg.targets:
            return []

        open_circuits = open_circuit_providers or set()
        latencies = provider_latencies or {}

        # 1. Filter out providers whose circuit is currently OPEN
        healthy_targets = [t for t in cfg.targets if t.provider not in open_circuits]
        if not healthy_targets:
            # If all are open, return empty list so caller can return 503
            return []

        strategy = cfg.strategy

        if strategy == "priority":
            # Retain defined order
            return healthy_targets

        elif strategy == "lowest_cost":
            # Sort by total estimated cost per million (input + output) ascending
            return sorted(
                healthy_targets,
                key=lambda t: (t.cost_input_per_million + t.cost_output_per_million),
            )

        elif strategy == "lowest_latency":
            # Sort by EWMA latency ascending (default 100ms if no historical data)
            return sorted(
                healthy_targets,
                key=lambda t: latencies.get(t.provider, 100.0),
            )

        elif strategy == "round_robin":
            # Weighted round-robin: build expanded weighted pool
            if alias_name not in self._round_robin_iterators:
                pool: list[TargetConfig] = []
                for t in cfg.targets:
                    pool.extend([t] * max(1, t.weight))
                self._round_robin_iterators[alias_name] = itertools.cycle(pool)

            chosen = next(self._round_robin_iterators[alias_name])
            # Put chosen target first, then remaining healthy targets as fallbacks
            ordered = [chosen] if chosen.provider not in open_circuits else []
            for t in healthy_targets:
                if t not in ordered:
                    ordered.append(t)
            return ordered if ordered else healthy_targets

        return healthy_targets


# Global router singleton
_router: Router | None = None


def get_router(config_path: str = "config/models.yaml") -> Router:
    """Returns or creates the global Router singleton."""
    global _router
    if _router is None:
        _router = Router(config_path=config_path)
    return _router
