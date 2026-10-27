"""Retry helper: exponential backoff with full jitter."""
import random


def backoff_delay(attempt: int, base: float = 0.25, cap: float = 3.0) -> float:
    """Seconds to wait before retry number `attempt` (1, 2, 3 ...).

    The ceiling doubles every attempt (0.5s, 1s, 2s ... up to `cap`) and the actual wait is a
    random value below it, so many clients that failed together do not retry together.
    """
    ceiling = min(cap, base * 2 ** attempt)
    return random.uniform(0, ceiling)
