"""Unit tests for alias loading and routing strategies in router.py."""

from relay.router import Router


def test_router_loads_config() -> None:
    """Verifies that Router loads aliases from models.yaml correctly."""
    router = Router(config_path="config/models.yaml")
    assert "fast" in router.aliases
    assert "smart" in router.aliases
    assert "cheap" in router.aliases

    models = router.list_models()
    ids = [m["id"] for m in models]
    assert "fast" in ids
    assert "smart" in ids


def test_router_priority_strategy() -> None:
    """Verifies that priority strategy maintains ordered targets and skips open circuits."""
    router = Router(config_path="config/models.yaml")
    # In config/models.yaml, smart has mock-a, mock-b, mock-c
    targets = router.plan_targets("smart")
    assert len(targets) >= 2
    assert targets[0].provider == "mock-a"

    # When mock-a circuit is open, it should skip to mock-b
    open_circuits = {"mock-a"}
    fallback_targets = router.plan_targets("smart", open_circuit_providers=open_circuits)
    assert len(fallback_targets) >= 1
    assert fallback_targets[0].provider == "mock-b"


def test_router_lowest_cost_strategy() -> None:
    """Verifies that lowest_cost strategy sorts targets by total cost."""
    router = Router(config_path="config/models.yaml")
    targets = router.plan_targets("cheap")
    assert len(targets) >= 2
    # mock-c gemini-flash is cheaper than mock-a gpt-4o-mini in config
    assert targets[0].provider == "mock-c"


def test_router_lowest_latency_strategy() -> None:
    """Verifies that lowest_latency strategy sorts targets by measured EWMA latency."""
    router = Router(config_path="config/models.yaml")
    latencies = {
        "mock-a": 150.0,
        "mock-b": 35.0,
    }
    targets = router.plan_targets("fast", provider_latencies=latencies)
    assert len(targets) == 2
    assert targets[0].provider == "mock-b"
