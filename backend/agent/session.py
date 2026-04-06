"""
ContentIQ — Agent: Session Store
In-memory session storage for multi-turn conversations.
Enables follow-up questions to skip re-retrieval.
Designed as a class so the backing store can be swapped to Redis
in v2 with zero API changes.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Session:
    conversation_id: str
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    last_entities: dict[str, Any] = field(default_factory=dict)
    source_label: str = "INTERNAL"


class SessionStore:
    """
    In-memory store for ContentIQ conversation sessions.

    Each session tracks:
        - retrieved_chunks: The chunks returned from the last retrieval call
                            (reused for follow-up questions)
        - history:          Chat history (user + assistant turns) for LLM context
        - last_entities:    Most recently queried entities (customer, topic)
        - source_label:     Whether last answer was INTERNAL or WEB
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, conversation_id: str) -> Session:
        """Get an existing session or create a new empty one."""
        if conversation_id not in self._sessions:
            self._sessions[conversation_id] = Session(conversation_id=conversation_id)
        return self._sessions[conversation_id]

    def update_retrieval(
        self,
        conversation_id: str,
        chunks: list[dict[str, Any]],
        entities: dict[str, Any],
        source_label: str = "INTERNAL",
    ) -> None:
        """Store newly retrieved chunks and the entities they were retrieved for."""
        session = self.get(conversation_id)
        session.retrieved_chunks = chunks
        session.last_entities = entities
        session.source_label = source_label
        logger.debug(
            "Session[%s] updated: %d chunks | entities=%s | source=%s",
            conversation_id, len(chunks), entities, source_label
        )

    def append_history(
        self,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """
        Append the latest user + assistant turn to session history.
        Caps history at 20 messages (10 turns) to prevent memory bloat.
        """
        session = self.get(conversation_id)
        session.history.append({"role": "user", "content": user_message})
        session.history.append({"role": "assistant", "content": assistant_message})
        # Keep last 20 messages (10 turns)
        if len(session.history) > 20:
            session.history = session.history[-20:]

    # Pronouns and short phrases that signal the user is referring back to the
    # last answer rather than asking a new question.
    _FOLLOWUP_SIGNALS = frozenset([
        "that", "this", "it", "they", "them", "those", "these",
        "he", "she", "who", "which", "what about", "tell me more",
        "explain", "elaborate", "summarise", "summarize", "expand",
        "why", "how so", "go on", "continue",
    ])

    def is_followup(
        self,
        new_entities: dict[str, Any],
        conversation_id: str,
        user_message: str = "",
    ) -> bool:
        """
        Determine whether the new query is a follow-up that can be answered
        from the session's cached chunks without a new retrieval call.

        A query is a follow-up when ALL of the following are true:
        1. The session has previously retrieved chunks.
        2. No new customer entity is introduced (same customer or none).
        3. The query looks like a refinement: it is short (≤ 8 words) OR
           starts with a pronoun/reference word that points back to prior context.

        The word-count + signal check prevents long, substantively different
        questions (e.g. "what % is it for Australia specifically") from
        incorrectly reusing stale chunks that don't contain the answer.
        """
        session = self.get(conversation_id)

        # No prior retrieval — must do fresh retrieval
        if not session.retrieved_chunks:
            return False

        new_customer = (new_entities.get("customer") or "").lower()
        last_customer = (session.last_entities.get("customer") or "").lower()

        # Different customer → fresh retrieval
        if new_customer and last_customer and new_customer != last_customer:
            logger.info(
                "Session[%s] → NEW RETRIEVAL: customer changed '%s' → '%s'",
                conversation_id, last_customer, new_customer
            )
            return False

        # New customer introduced for first time → fresh retrieval
        if new_customer and not last_customer:
            logger.info(
                "Session[%s] → NEW RETRIEVAL: new customer entity '%s' introduced.",
                conversation_id, new_customer
            )
            return False

        # Check whether the message looks like a genuine follow-up refinement.
        # Require BOTH short (≤5 words) AND a signal word, or just a signal word
        # on its own. The old ≤8-word-only check was too broad — 7-word queries
        # like "Name all the aircrafts that indigo uses" are substantive new
        # questions, not follow-ups.
        words = user_message.lower().split()
        starts_with_signal = bool(words) and any(
            user_message.lower().startswith(signal)
            for signal in self._FOLLOWUP_SIGNALS
        )
        is_short = len(words) <= 5

        if not (is_short or starts_with_signal):
            logger.info(
                "Session[%s] → NEW RETRIEVAL: message is substantive (%d words, "
                "no follow-up signal).",
                conversation_id, len(words)
            )
            return False

        logger.info(
            "Session[%s] → FOLLOW-UP: reusing %d cached chunks.",
            conversation_id, len(session.retrieved_chunks)
        )
        return True
