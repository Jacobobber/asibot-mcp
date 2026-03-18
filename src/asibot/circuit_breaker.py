"""Per-service circuit breakers to fail fast when a backend is down.

States: CLOSED (normal) -> OPEN (failing, reject fast) -> HALF_OPEN (probe).

Usage::

    from asibot.circuit_breaker import get_breaker

    breaker = get_breaker("github")
    if not breaker.can_execute():
        return None, f"github is temporarily unavailable (circuit open, retry in {breaker.time_until_recovery:.0f}s)"

    # ... make request ...
    breaker.record_success()
    # or
    breaker.record_failure()
"""

import asyncio
import time

# --- Configuration ---

_DEFAULT_FAILURE_THRESHOLD = 5  # consecutive failures to open
_DEFAULT_RECOVERY_TIMEOUT = 60  # seconds in OPEN before HALF_OPEN
_DEFAULT_HALF_OPEN_MAX = 1  # probe requests allowed in HALF_OPEN

# --- Circuit Breaker ---


class CircuitBreaker:
    """Per-service circuit breaker with three states.

    Thread-safe via asyncio.Lock for state transitions.
    can_execute() is synchronous (reads only) so callers don't need to await
    the common-path check; the lock is used in record_success/record_failure
    to prevent concurrent state mutations.
    """

    def __init__(
        self,
        service: str,
        *,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: float = _DEFAULT_RECOVERY_TIMEOUT,
        half_open_max: int = _DEFAULT_HALF_OPEN_MAX,
    ) -> None:
        self.service = service
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max

        self._lock = asyncio.Lock()
        self._consecutive_failures: int = 0
        self._state: str = "closed"
        self._opened_at: float = 0.0  # timestamp when circuit opened
        self._half_open_attempts: int = 0

    # --- Public read-only properties ---

    @property
    def state(self) -> str:
        """Return current state, auto-transitioning OPEN -> HALF_OPEN on timeout."""
        if self._state == "open" and time.monotonic() - self._opened_at >= self.recovery_timeout:
            return "half_open"
        return self._state

    @property
    def time_until_recovery(self) -> float:
        """Seconds remaining until HALF_OPEN probe is allowed. 0 if not open."""
        if self._state != "open":
            return 0.0
        elapsed = time.monotonic() - self._opened_at
        remaining = self.recovery_timeout - elapsed
        return max(0.0, remaining)

    # --- Execution check ---

    def can_execute(self) -> bool:
        """Return True if a request is allowed through the breaker.

        CLOSED: always allowed.
        OPEN: blocked until recovery_timeout elapses.
        HALF_OPEN: allowed up to half_open_max probe requests.
        """
        current = self.state  # triggers OPEN -> HALF_OPEN transition check

        if current == "closed":
            return True

        if current == "open":
            return False

        # half_open — allow limited probes
        if current == "half_open":
            if self._half_open_attempts < self.half_open_max:
                # Lazily commit the state transition on first probe
                if self._state == "open":
                    self._state = "half_open"
                    self._half_open_attempts = 0
                self._half_open_attempts += 1
                return True
            return False

        return False  # unreachable, defensive

    # --- State mutation (async for thread safety) ---

    async def record_success(self) -> None:
        """Record a successful request. Closes the circuit."""
        async with self._lock:
            self._consecutive_failures = 0
            if self._state in ("half_open", "open"):
                self._state = "closed"
                self._half_open_attempts = 0

    async def record_failure(self) -> None:
        """Record a failed request. May open or reopen the circuit."""
        async with self._lock:
            self._consecutive_failures += 1

            if self._state == "half_open":
                # Probe failed — reopen immediately
                self._state = "open"
                self._opened_at = time.monotonic()
                self._half_open_attempts = 0
                return

            if self._state == "closed" and self._consecutive_failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()
                self._half_open_attempts = 0

    async def reset(self) -> None:
        """Manually reset the breaker to closed state."""
        async with self._lock:
            self._state = "closed"
            self._consecutive_failures = 0
            self._half_open_attempts = 0
            self._opened_at = 0.0


# --- Global Registry ---

_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(service: str) -> CircuitBreaker:
    """Get or create a circuit breaker for the given service name.

    Breakers are singletons per service — all users share the same breaker
    for a given backend, since a downed service affects everyone.
    """
    breaker = _breakers.get(service)
    if breaker is None:
        breaker = CircuitBreaker(service)
        _breakers[service] = breaker
    return breaker


def all_breaker_states() -> dict[str, str]:
    """Return a snapshot of all breaker states for health checks.

    Returns: {"github": "closed", "zendesk": "open", ...}
    """
    return {name: breaker.state for name, breaker in _breakers.items()}
