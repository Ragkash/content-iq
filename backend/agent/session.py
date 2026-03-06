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

    def is_followup(
        self,
        new_entities: dict[str, Any],
        conversation_id: str,
    ) -> bool:
        """
        Determine whether the new query is a follow-up in the same context.

        A query is a follow-up when:
        1. The session has previously retrieved chunks, AND
        2. No NEW customer entity is introduced (same or no customer)

        This prevents unnecessarily re-running retrieval for questions like
        "Who wrote that?" when we already have Shell proposals in session.

        Args:
            new_entities:      Parsed entities from the new user query.
            conversation_id:   The active conversation ID.

        Returns:
            True if this should be answered from session cache.
        """
        session = self.get(conversation_id)

        # No prior retrieval — must do fresh retrieval
        if not session.retrieved_chunks:
            return False

        new_customer = (new_entities.get("customer") or "").lower()
        last_customer = (session.last_entities.get("customer") or "").lower()

        # If user asks about a different customer → fresh retrieval
        if new_customer and last_customer and new_customer != last_customer:
            logger.info(
                "Session[%s] → NEW RETRIEVAL: customer changed '%s' → '%s'",
                conversation_id, last_customer, new_customer
            )
            return False

        # If user introduces a customer entity for the first time → fresh retrieval
        if new_customer and not last_customer:
            logger.info(
                "Session[%s] → NEW RETRIEVAL: new customer entity '%s' introduced.",
                conversation_id, new_customer
            )
            return False

        logger.info(
            "Session[%s] → FOLLOW-UP: reusing %d cached chunks.",
            conversation_id, len(session.retrieved_chunks)
        )
        return True
