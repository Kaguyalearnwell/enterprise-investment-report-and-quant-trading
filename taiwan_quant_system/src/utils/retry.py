"""Rate limiting and retry with exponential backoff.

`RateLimiter` enforces two limits at once:
  * a minimum interval between calls (Stage 2.1: bursts < 1 s apart briefly returned empty data);
  * a sliding one-hour quota (FinMind: 300/h anonymous, 600/h registered token).
`clock` and `sleep` are injectable so tests run instantly and deterministically.
"""
from __future__ import annotations

import logging
import random
import time
from collections import deque
from collections.abc import Callable
from typing import TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")


class RateLimiter:
    def __init__(self, max_per_hour: int, min_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, window: float = 3600.0):
        if max_per_hour < 1:
            raise ValueError("max_per_hour must be >= 1")
        self.max_per_hour = max_per_hour
        self.min_interval = min_interval
        self.window = window
        self._clock, self._sleep = clock, sleep
        self._calls: deque[float] = deque()

    def acquire(self) -> float:
        """Block until a call is allowed; return the seconds waited."""
        waited = 0.0
        while True:
            now = self._clock()
            while self._calls and now - self._calls[0] >= self.window:
                self._calls.popleft()
            wait = 0.0
            if self._calls:
                wait = max(wait, self.min_interval - (now - self._calls[-1]))
            if len(self._calls) >= self.max_per_hour:
                wait = max(wait, self.window - (now - self._calls[0]))
            if wait <= 0:
                self._calls.append(now)
                return waited
            if wait > 60:
                log.info("hourly quota reached (%d calls); waiting %.0f s", len(self._calls), wait)
            self._sleep(wait)
            waited += wait

    def penalize(self, seconds: float) -> None:
        """Server said we are over quota: block every caller for `seconds`."""
        log.warning("server rate limit hit; pausing %.0f s", seconds)
        self._sleep(seconds)

    @property
    def calls_in_window(self) -> int:
        now = self._clock()
        return sum(1 for t in self._calls if now - t < self.window)


class TransientError(Exception):
    """Worth retrying: network failure, timeout, HTTP 5xx, unexpected empty body."""


def retry(fn: Callable[[], T], attempts: int = 5, base_delay: float = 2.0, max_delay: float = 120.0,
          retry_on: tuple[type[BaseException], ...] = (TransientError,),
          sleep: Callable[[float], None] = time.sleep, jitter: bool = True) -> T:
    """Call fn(); on a retryable error wait base*2^k (+jitter) and try again, up to `attempts` times."""
    for k in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if k == attempts:
                raise
            delay = min(max_delay, base_delay * 2 ** (k - 1))
            if jitter:
                delay *= 1 + 0.25 * random.random()
            log.warning("attempt %d/%d failed (%s); retrying in %.1f s", k, attempts, exc, delay)
            sleep(delay)
    raise AssertionError("unreachable")
