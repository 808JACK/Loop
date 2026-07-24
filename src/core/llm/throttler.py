"""
LLM Request Throttler — token-bucket rate limiter + retry-with-backoff.

All LLM calls go through this singleton so we never burst past the Ollama
Cloud session usage limit.  Configure via:
  LLM_REQUESTS_PER_MINUTE  (default: 8)
  LLM_MAX_RETRIES          (default: 3)
  LLM_RETRY_BASE_DELAY     (default: 15 seconds)
"""

import logging
import re
import threading
import time
from typing import Any

logger = logging.getLogger("llm_throttler")


class RateLimitError(Exception):
    """Raised when the LLM API returns 429 and all retries are exhausted."""


class SessionLimitError(RateLimitError):
    """
    Raised when the API returns 429 due to session/account usage quota exhaustion.

    Unlike per-minute throttling, waiting and retrying won't help here — the user
    needs to reset their API key or wait for the session to reset.
    Workflow is paused immediately with checkpoint preserved for resume.
    """


class LLMThrottler:
    """
    Thread-safe token-bucket rate limiter for LLM API calls.

    Ensures at most `requests_per_minute` LLM calls per minute.
    On 429 responses, it shares a cooldown across all callers and keeps
    retrying the same queued request until the API becomes available again.
    """

    def __init__(
        self,
        requests_per_minute: int = 8,
        max_retries: int = 3,
        retry_base_delay: float = 15.0,
        queue_timeout_seconds: float = 900.0,
    ):
        """Initialize the LLMThrottler."""
        self.requests_per_minute = requests_per_minute
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.queue_timeout_seconds = queue_timeout_seconds

        # Token bucket state
        self._lock = threading.Lock()
        self._cooldown_cond = threading.Condition(self._lock)
        self._cooldown_until = 0.0
        self._tokens = float(requests_per_minute)
        self._max_tokens = float(requests_per_minute)
        self._last_refill = time.monotonic()
        self._refill_rate = requests_per_minute / 60.0  # tokens per second

    # ------------------------------------------------------------------
    # Token bucket
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._max_tokens,
            self._tokens + elapsed * self._refill_rate,
        )
        self._last_refill = now

    def _wait_for_cooldown(self) -> None:
        """Block all callers while a shared 429 cooldown is active."""
        with self._cooldown_cond:
            while True:
                now = time.monotonic()
                remaining = self._cooldown_until - now
                if remaining <= 0:
                    return
                logger.warning(
                    f"[throttler] Shared 429 cooldown active; sleeping {remaining:.1f}s "
                    "before allowing more LLM calls"
                )
                self._cooldown_cond.wait(timeout=remaining)

    def _set_cooldown(self, delay: float) -> None:
        """Start or extend the shared cooldown window for the whole process."""
        with self._cooldown_cond:
            new_until = time.monotonic() + max(0.0, delay)
            if new_until > self._cooldown_until:
                self._cooldown_until = new_until
            self._cooldown_cond.notify_all()

    def _acquire(self) -> None:
        """
        Block until a token is available, then consume it.

        This enforces the requests-per-minute ceiling.
        """
        while True:
            self._wait_for_cooldown()
            with self._lock:
                self._refill()
                if time.monotonic() < self._cooldown_until:
                    continue
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Calculate wait time until next token
                wait = (1.0 - self._tokens) / self._refill_rate

            logger.info(
                f"[throttler] Rate limit: sleeping {wait:.1f}s before next LLM call "
                f"(budget: {self._tokens:.2f}/{self._max_tokens} tokens)"
            )
            time.sleep(wait)

    @staticmethod
    def _extract_retry_delay_seconds(error: Exception) -> float | None:
        """Try to recover a server-provided retry delay from the error text."""
        error_str = str(error)
        match = re.search(r"try again in\s+(\d+(?:\.\d+)?)s", error_str, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except Exception:
                return None
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def invoke(self, chain: Any, inputs: dict[str, Any]) -> Any:
        """
        Invoke a LangChain chain/runnable with throttling and retry logic.

        Args:
            chain:  Any LangChain Runnable (e.g. prompt | llm)
            inputs: Dict of inputs to pass to chain.invoke()

        Returns:
            The result of chain.invoke(inputs)

        Raises:
            Exception: Any non-rate-limit error from the chain is re-raised as-is.
        """
        attempt = 0

        while True:
            # Wait for any global cooldown before making the call.
            self._wait_for_cooldown()

            # Wait for a token slot before making the call.
            self._acquire()

            try:
                return chain.invoke(inputs)

            except Exception as e:
                error_str = str(e).lower()

                is_throttle = (
                    "429" in str(e)
                    or "rate limit" in error_str
                    or "too many requests" in error_str
                    or "usage limit" in error_str
                )
                is_transient_transport = (
                    "server disconnected without sending a response" in error_str
                    or "remoteprotocolerror" in error_str
                    or "connection reset" in error_str
                    or "connection aborted" in error_str
                    or "httpx.ReadError" in str(e)
                    or "httpx.RemoteProtocolError" in str(e)
                    or "httpcore.RemoteProtocolError" in str(e)
                )
                if not is_throttle and not is_transient_transport:
                    raise

                server_delay = self._extract_retry_delay_seconds(e)
                delay = (
                    server_delay
                    if server_delay is not None
                    else self.retry_base_delay * (2**attempt)
                )
                delay = max(1.0, delay)
                self._set_cooldown(delay)
                if is_throttle:
                    logger.warning(
                        f"[throttler] 429 rate throttle (attempt {attempt + 1}). "
                        f"Paused all queued LLM requests for {delay:.0f}s. Error: {e}"
                    )
                else:
                    logger.warning(
                        f"[throttler] transient transport error (attempt {attempt + 1}). "
                        f"Paused all queued LLM requests for {delay:.0f}s and will retry. "
                        f"Error: {e}"
                    )
                self._wait_for_cooldown()
                attempt += 1


# ---------------------------------------------------------------------------
# Module-level singleton — initialised lazily from settings
# ---------------------------------------------------------------------------

_throttler: LLMThrottler | None = None
_throttler_lock = threading.Lock()


def get_throttler() -> LLMThrottler:
    """Return (or create) the global LLMThrottler singleton."""
    global _throttler
    if _throttler is None:
        with _throttler_lock:
            if _throttler is None:
                try:
                    from src.settings import settings

                    rpm = getattr(settings, "llm_requests_per_minute", 8)
                    retries = getattr(settings, "llm_max_retries", 3)
                    base_delay = getattr(settings, "llm_retry_base_delay", 15)
                    queue_timeout = getattr(settings, "llm_queue_timeout_seconds", 900)
                except Exception:
                    rpm, retries, base_delay, queue_timeout = 8, 3, 15, 900

                logger.info(
                    f"[throttler] Initialised: {rpm} req/min, "
                    f"max_retries={retries}, base_delay={base_delay}s, "
                    f"queue_timeout={queue_timeout}s"
                )
                _throttler = LLMThrottler(
                    requests_per_minute=rpm,
                    max_retries=retries,
                    retry_base_delay=float(base_delay),
                    queue_timeout_seconds=float(queue_timeout),
                )
    return _throttler


def call_llm(chain: Any, inputs: dict[str, Any]) -> Any:
    """
    Throttle and invoke a LangChain chain.

    Usage (replace direct chain.invoke() calls):
        result = call_llm(prompt | llm, {"key": "value"})
    """
    return get_throttler().invoke(chain, inputs)
