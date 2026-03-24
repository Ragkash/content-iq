"""
ContentIQ — Shared Groq client with round-robin key rotation and 429 retry.

Reads GROQ_API_KEY_1, GROQ_API_KEY_2, GROQ_API_KEY_3 from .env and cycles
through them sequentially so consecutive requests distribute across keys.
All chat completion calls should use chat_completion() which retries on
rate-limit (429) errors with exponential backoff.
"""

import os
import itertools
import logging
import time
from typing import Any

from groq import Groq, RateLimitError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

_RETRY_DELAYS = [5, 15, 30]   # seconds between attempts after a 429


def _load_clients() -> list[Groq]:
    keys = [
        os.getenv(f"GROQ_API_KEY_{i}", "").strip()
        for i in range(1, 4)
    ]
    valid = [k for k in keys if k]
    if not valid:
        raise EnvironmentError(
            "At least one of GROQ_API_KEY_1, GROQ_API_KEY_2, GROQ_API_KEY_3 must be set in .env"
        )
    logger.debug("Groq key rotation: %d key(s) loaded.", len(valid))
    return [Groq(api_key=k) for k in valid]


_clients = _load_clients()
_cycle = itertools.cycle(_clients)


def get_client() -> Groq:
    """Return the next Groq client in round-robin order (thread-safe via GIL)."""
    return next(_cycle)


def chat_completion(messages: list[dict[str, str]], **kwargs: Any):
    """
    Call Groq chat completions with automatic key rotation and 429 retry.

    Rotates to the next key on each attempt so that rate-limit recovery
    benefits from any per-key headroom. Raises the last exception if all
    retries are exhausted.
    """
    last_exc: Exception | None = None
    attempts = 1 + len(_RETRY_DELAYS)
    for attempt in range(attempts):
        try:
            client = get_client()
            return client.chat.completions.create(
                messages=messages,  # type: ignore[arg-type]
                **kwargs,
            )
        except RateLimitError as exc:
            last_exc = exc
            if attempt < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "Groq 429 rate limit (attempt %d/%d) — retrying in %ds",
                    attempt + 1, attempts, delay,
                )
                time.sleep(delay)
            else:
                logger.error("Groq rate limit: all %d attempts exhausted.", attempts)
    raise last_exc  # type: ignore[misc]
