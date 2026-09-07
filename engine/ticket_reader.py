"""TicketReader: loads and parses tickets by ID from the TicketStore."""

from dataclasses import dataclass, field
from typing import Dict, Optional

from engine.ticket_store import TicketStore


@dataclass
class Ticket:
    """Parsed representation of a support ticket."""

    id: str
    title: str
    description: str
    status: str
    priority: str
    category: str
    created_at: str
    updated_at: str
    requester: str
    metadata: Dict = field(default_factory=dict)

    def __str__(self) -> str:
        lines = [
            f"Ticket  : {self.id}",
            f"Title   : {self.title}",
            f"Status  : {self.status}  |  Priority: {self.priority}  |  Category: {self.category}",
            f"Requester: {self.requester}",
            f"Created : {self.created_at}",
            f"Description:\n  {self.description}",
        ]
        if self.metadata:
            meta_str = ", ".join(f"{k}={v}" for k, v in self.metadata.items())
            lines.append(f"Metadata: {meta_str}")
        return "\n".join(lines)


class TicketReader:
    """Reads and parses tickets from a TicketStore."""

    def __init__(self, store: Optional[TicketStore] = None):
        self.store = store or TicketStore()

    def load(self, ticket_id: str) -> Optional[Ticket]:
        """Load and parse a ticket by ID. Returns None if not found."""
        raw = self.store.get(ticket_id)
        if raw is None:
            return None
        return self._parse(raw)

    def load_all(self):
        """Load and parse all tickets. Returns a list of Ticket objects."""
        return [self._parse(raw) for raw in self.store.all()]

    @staticmethod
    def _parse(raw: dict) -> Ticket:
        """Convert a raw dict into a Ticket dataclass."""
        return Ticket(
            id=raw["id"],
            title=raw["title"],
            description=raw["description"],
            status=raw["status"],
            priority=raw["priority"],
            category=raw["category"],
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            requester=raw["requester"],
            metadata=raw.get("metadata", {}),
        )
