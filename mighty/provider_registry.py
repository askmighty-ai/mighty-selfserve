"""Provider Registry — managed-provider catalog for the Provider Manager.

Elevates the dashboard from a single hard-coded Amex card to a provider-platform
surface. Registration is the seam for adding providers: dashboard and API discover
managed providers from this registry rather than branching on provider ids.

Does not own runtime verification, keepalive, recovery, publishing, or AccessState
contracts. Those remain provider-independent and unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Iterable

from mighty.provider_policy import (
    ProviderPolicy,
    amex_provider_policy,
    default_provider_policy,
)


@dataclass(frozen=True)
class ProviderPlatformCapabilities:
    """Platform capabilities the dashboard/ops UI may surface for a provider.

    Distinct from ConnectorCapabilities (field/read coverage). These flags describe
    what the managed runtime surface supports so the UI does not hard-code Amex
    assumptions.
    """

    verification: bool = False
    keepalive: bool = False
    recovery: bool = False
    snapshots: bool = False
    connector_readiness: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification": self.verification,
            "keepalive": self.keepalive,
            "recovery": self.recovery,
            "snapshots": self.snapshots,
            "connector_readiness": self.connector_readiness,
        }

    def enabled_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, enabled in (
                ("verification", self.verification),
                ("keepalive", self.keepalive),
                ("recovery", self.recovery),
                ("snapshots", self.snapshots),
                ("connector_readiness", self.connector_readiness),
            )
            if enabled
        )


@dataclass(frozen=True)
class ManagedProvider:
    """One managed provider entry in the Provider Registry."""

    provider_id: str
    display_name: str
    capabilities: ProviderPlatformCapabilities
    open_url: str | None = None
    sort_order: int = 100
    policy: ProviderPolicy = field(default_factory=default_provider_policy)

    def __post_init__(self) -> None:
        pid = str(self.provider_id or "").strip().lower()
        if not pid:
            raise ValueError("provider_id is required")
        if not str(self.display_name or "").strip():
            raise ValueError("display_name is required")
        object.__setattr__(self, "provider_id", pid)
        if not isinstance(self.policy, ProviderPolicy):
            raise TypeError("policy must be a ProviderPolicy")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "capabilities": self.capabilities.to_dict(),
            "open_url": self.open_url,
            "sort_order": self.sort_order,
            "policy": self.policy.to_dict(),
        }


class ProviderRegistry:
    """In-process registry of managed providers.

    Adding a provider means calling ``register`` (or a helper like
    ``register_amex``). Dashboard list rendering and access-state read paths
    discover providers here — they must not hard-code provider ids.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ManagedProvider] = {}
        self._lock = RLock()

    def register(self, provider: ManagedProvider) -> ManagedProvider:
        if not isinstance(provider, ManagedProvider):
            raise TypeError("provider must be a ManagedProvider")
        with self._lock:
            self._providers[provider.provider_id] = provider
        return provider

    def unregister(self, provider_id: str) -> bool:
        with self._lock:
            return self._providers.pop(str(provider_id).strip().lower(), None) is not None

    def clear(self) -> None:
        with self._lock:
            self._providers.clear()

    def get(self, provider_id: str) -> ManagedProvider | None:
        return self._providers.get(str(provider_id or "").strip().lower())

    def require(self, provider_id: str) -> ManagedProvider:
        provider = self.get(provider_id)
        if provider is None:
            raise KeyError(f"provider not registered: {provider_id}")
        return provider

    def is_registered(self, provider_id: str) -> bool:
        return self.get(provider_id) is not None

    def list_providers(self) -> tuple[ManagedProvider, ...]:
        with self._lock:
            items = list(self._providers.values())
        items.sort(key=lambda p: (p.sort_order, p.display_name.lower(), p.provider_id))
        return tuple(items)

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(p.provider_id for p in self.list_providers())

    def display_name(self, provider_id: str) -> str:
        provider = self.get(provider_id)
        if provider is not None:
            return provider.display_name
        pid = str(provider_id or "").strip()
        return pid.title() if pid else "Provider"

    def capabilities_for(
        self, provider_id: str
    ) -> ProviderPlatformCapabilities:
        provider = self.get(provider_id)
        if provider is not None:
            return provider.capabilities
        return ProviderPlatformCapabilities()

    def policy_for(self, provider_id: str) -> ProviderPolicy:
        provider = self.get(provider_id)
        if provider is not None:
            return provider.policy
        return default_provider_policy()

    def replace_all(self, providers: Iterable[ManagedProvider]) -> None:
        """Atomically replace registry contents (test helper)."""
        mapped = {p.provider_id: p for p in providers}
        with self._lock:
            self._providers = mapped


AMEX_OPEN_URL = "https://www.americanexpress.com/en-us/account/login"

AMEX_PLATFORM_CAPABILITIES = ProviderPlatformCapabilities(
    verification=True,
    keepalive=True,
    recovery=True,
    snapshots=True,
    connector_readiness=True,
)


def build_amex_provider() -> ManagedProvider:
    """Build the Amex ManagedProvider registration (does not mutate a registry)."""
    return ManagedProvider(
        provider_id="amex",
        display_name="American Express",
        capabilities=AMEX_PLATFORM_CAPABILITIES,
        open_url=AMEX_OPEN_URL,
        sort_order=10,
        policy=amex_provider_policy(),
    )


def register_amex(registry: ProviderRegistry | None = None) -> ManagedProvider:
    """Register Amex on the given registry (default: process registry)."""
    target = registry if registry is not None else get_provider_registry()
    return target.register(build_amex_provider())


_REGISTRY: ProviderRegistry | None = None
_REGISTRY_LOCK = RLock()


def get_provider_registry() -> ProviderRegistry:
    """Return the process-wide Provider Registry, ensuring Amex is registered."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = ProviderRegistry()
            register_amex(_REGISTRY)
        return _REGISTRY


def reset_provider_registry_for_tests(
    *,
    providers: Iterable[ManagedProvider] | None = None,
    include_amex: bool = True,
) -> ProviderRegistry:
    """Replace the process registry (tests only)."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        registry = ProviderRegistry()
        if providers is not None:
            registry.replace_all(providers)
        elif include_amex:
            register_amex(registry)
        _REGISTRY = registry
        return registry
