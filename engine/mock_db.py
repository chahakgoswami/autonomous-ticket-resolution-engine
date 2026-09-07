"""Mock database layer backed by a JSON file, loaded into memory."""

import json
import os
from typing import Any, Dict, List, Optional


DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "mock_db.json"
)


class MockDatabase:
    """In-memory mock database loaded from JSON.

    Tables supported: users, orders, inventory.
    All mutations happen in memory only — no files are written.
    """

    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        self._data: Dict[str, List[dict]] = {}
        self._load()

    def _load(self) -> None:
        """Load the JSON database into memory."""
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Mock DB not found: {self.path}")
        with open(self.path, "r", encoding="utf-8") as fh:
            self._data = json.load(fh)

    # ------------------------------------------------------------------
    # Generic table access
    # ------------------------------------------------------------------

    def table(self, table_name: str) -> List[dict]:
        """Return a copy of all rows in a table."""
        if table_name not in self._data:
            raise KeyError(f"Unknown table: {table_name}")
        return list(self._data[table_name])

    def tables(self) -> List[str]:
        """Return the list of available table names."""
        return list(self._data.keys())

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def find_by(self, table_name: str, field: str, value: Any) -> List[dict]:
        """Return rows where row[field] == value."""
        rows = self.table(table_name)
        return [r for r in rows if r.get(field) == value]

    def find_one_by(self, table_name: str, field: str, value: Any) -> Optional[dict]:
        """Return the first row where row[field] == value, or None."""
        results = self.find_by(table_name, field, value)
        return results[0] if results else None

    # ------------------------------------------------------------------
    # Mutation helpers (in-memory only)
    # ------------------------------------------------------------------

    def update_row(self, table_name: str, match_field: str, match_value: Any,
                   updates: Dict[str, Any]) -> int:
        """Update fields on all rows matching match_field == match_value.

        Returns the number of rows updated.
        """
        count = 0
        for row in self._data.get(table_name, []):
            if row.get(match_field) == match_value:
                row.update(updates)
                count += 1
        return count

    def delete_rows(self, table_name: str, match_field: str, match_value: Any) -> int:
        """Delete rows where match_field == match_value.

        Returns the number of rows deleted.
        """
        original = self._data.get(table_name, [])
        kept = [r for r in original if r.get(match_field) != match_value]
        removed = len(original) - len(kept)
        self._data[table_name] = kept
        return removed

    def delete_rows_where(self, table_name: str, predicate) -> int:
        """Delete rows matching an arbitrary predicate function.

        Returns the number of rows deleted.
        """
        original = self._data.get(table_name, [])
        kept = [r for r in original if not predicate(r)]
        removed = len(original) - len(kept)
        self._data[table_name] = kept
        return removed
