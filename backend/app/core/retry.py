"""Retry helper with exponential backoff and jitter.

Provider SDKs already retry some failures, but not consistently or with the
backoff we want. The agent runner therefore retries at this layer and records
the attempt count for observability.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_call(
    func: Callable[[], T],
    *,
    retries: int,
    base_delay: float,
    max_delay: float,
    retry_if: Callable[[Exception], bool] | None = None,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> T:
    """Call ``func`` until it succeeds or the retry budget is exhausted.

    Args:
        func: Zero-argument callable to execute.
        retries: Number of retries *after* the initial attempt.
        base_delay: Delay before the first retry, in seconds.
        max_delay: Upper bound for a single backoff sleep.
        retry_if: Predicate deciding whether an exception is retryable.
        on_retry: Optional callback ``(attempt, exception, delay)``.

    Raises:
        The last exception when every attempt fails.
    """
    attempt = 0
    while True:
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            if attempt >= retries or (retry_if is not None and not retry_if(exc)):
                raise
            attempt += 1
            # Full jitter: avoids thundering herds when many workers hit a 429.
            ceiling = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = random.uniform(0, ceiling)
            logger.warning(
                "Retry %d/%d after %.2fs due to %s: %s",
                attempt,
                retries,
                delay,
                type(exc).__name__,
                exc,
            )
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            time.sleep(delay)
