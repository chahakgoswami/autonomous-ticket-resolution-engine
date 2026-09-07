"""JSON-backed mock ticket store."""

import json
import os
from typing import Dict, List, Optional


DEFAULT_TICKETS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "tickets.json"
)


class TicketStore:
    """Loads and manages tickets from a JSON file."""

    def __init__(self, path: str = DEFAULT_TICKETS_PATH):
        self.path = path
        self._tickets: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Load tickets from the JSON file into an in-memory dict keyed by ID."""
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Ticket store not found: {self.path}")
        with open(self.path, "r", encoding="utf-8") as fh:
            raw: List[dict] = json.load(fh)
        self._tickets = {ticket["id"]: ticket for ticket in raw}

    def get(self, ticket_id: str) -> Optional[dict]:
        """Return a ticket dict by ID, or None if not found."""
        return self._tickets.get(ticket_id)

    def all(self) -> List[dict]:
        """Return all tickets as a list."""
        return list(self._tickets.values())

    def ids(self) -> List[str]:
        """Return all ticket IDs."""
        return list(self._tickets.keys())

    def update_status(self, ticket_id: str, status: str) -> bool:
        """Update the status of a ticket in memory. Returns True on success."""
        if ticket_id not in self._tickets:
            return False
        self._tickets[ticket_id]["status"] = status
        return True
