"""QueryEngine: maps natural-language ticket intent to mock DB queries."""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.mock_db import MockDatabase

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    """Structured result returned by the QueryEngine."""

    intent: str
    query_description: str
    table: str
    filters: Dict[str, Any]
    rows: List[dict] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None

    def __str__(self) -> str:
        lines = [
            f"Intent          : {self.intent}",
            f"Query           : {self.query_description}",
            f"Table           : {self.table}",
            f"Filters         : {self.filters}",
        ]
        if self.error:
            lines.append(f"Error           : {self.error}")
        else:
            lines.append(f"Rows returned   : {len(self.rows)}")
            for i, row in enumerate(self.rows, 1):
                lines.append(f"  [{i}] {row}")
        return "\n".join(lines)


class QueryEngine:
    """Maps natural-language ticket intent to mock DB queries.

    Supported intents (matched by keyword scanning):
      - find user / lookup user / get user
      - check order / find order / get order / lookup order
      - check inventory / find inventory / get inventory / lookup inventory / check stock
      - list users / list orders / list inventory
      - find test users / delete test users  (special pattern for *@test.example.com)
    """

    # Intent keyword groups mapped to a handler name
    _INTENT_MAP = [
        # (set of trigger keywords, handler_name)
        ({"find user", "lookup user", "get user", "check user"}, "find_user"),
        ({"find order", "lookup order", "get order", "check order"}, "find_order"),
        ({"check inventory", "find inventory", "get inventory",
          "lookup inventory", "check stock"}, "check_inventory"),
        ({"list users"}, "list_users"),
        ({"list orders"}, "list_orders"),
        ({"list inventory"}, "list_inventory"),
        ({"find test", "delete test", "test user", "test records"}, "find_test_users"),
    ]

    def __init__(self, db: Optional[MockDatabase] = None):
        self.db = db or MockDatabase()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, intent: str, context: Dict[str, Any] = None) -> QueryResult:
        """Interpret *intent* (free text) and execute the matching DB query.

        Args:
            intent:  Natural-language description of what to query.
            context: Optional dict of parameters extracted from the ticket
                     (e.g. {'email': 'john.doe@example.com', 'order_id': '78432'}).

        Returns:
            A :class:`QueryResult` with matched rows and metadata.
        """
        context = context or {}
        handler_name = self._classify_intent(intent)

        if handler_name is None:
            result = QueryResult(
                intent=intent,
                query_description="<no matching query>",
                table="",
                filters={},
                error=f"Unrecognised intent: '{intent}'",
            )
            self._log_result(result)
            return result

        handler = getattr(self, f"_q_{handler_name}")
        result = handler(intent, context)
        self._log_result(result)
        return result

    # ------------------------------------------------------------------
    # Intent classification
    # ------------------------------------------------------------------

    def _classify_intent(self, intent: str) -> Optional[str]:
        """Return the handler name for the given intent string, or None."""
        normalised = intent.lower().strip()
        for keywords, handler in self._INTENT_MAP:
            for kw in keywords:
                if kw in normalised:
                    return handler
        return None

    # ------------------------------------------------------------------
    # Query handlers
    # ------------------------------------------------------------------

    def _q_find_user(self, intent: str, ctx: Dict[str, Any]) -> QueryResult:
        """SELECT * FROM users WHERE email = <email> (or id)."""
        email = ctx.get("email") or ctx.get("user_email")
        user_id = ctx.get("user_id")

        if email:
            filters = {"email": email}
            desc = f"SELECT * FROM users WHERE email = '{email}'"
            rows = self.db.find_by("users", "email", email)
        elif user_id is not None:
            filters = {"id": user_id}
            desc = f"SELECT * FROM users WHERE id = {user_id}"
            rows = self.db.find_by("users", "id", user_id)
        else:
            filters = {}
            desc = "SELECT * FROM users  -- no filter (full scan)"
            rows = self.db.table("users")

        return QueryResult(
            intent=intent,
            query_description=desc,
            table="users",
            filters=filters,
            rows=rows,
        )

    def _q_find_order(self, intent: str, ctx: Dict[str, Any]) -> QueryResult:
        """SELECT * FROM orders WHERE id = <order_id> (or customer_email)."""
        order_id_raw = ctx.get("order_id")
        customer_email = ctx.get("customer_email")

        if order_id_raw is not None:
            # order IDs are stored as integers in mock_db
            try:
                order_id = int(order_id_raw)
            except (TypeError, ValueError):
                order_id = order_id_raw
            filters = {"id": order_id}
            desc = f"SELECT * FROM orders WHERE id = {order_id}"
            rows = self.db.find_by("orders", "id", order_id)
        elif customer_email:
            filters = {"customer_email": customer_email}
            desc = f"SELECT * FROM orders WHERE customer_email = '{customer_email}'"
            rows = self.db.find_by("orders", "customer_email", customer_email)
        else:
            filters = {}
            desc = "SELECT * FROM orders  -- no filter (full scan)"
            rows = self.db.table("orders")

        return QueryResult(
            intent=intent,
            query_description=desc,
            table="orders",
            filters=filters,
            rows=rows,
        )

    def _q_check_inventory(self, intent: str, ctx: Dict[str, Any]) -> QueryResult:
        """SELECT * FROM inventory WHERE sku = <sku>."""
        sku = ctx.get("sku")

        if sku:
            filters = {"sku": sku}
            desc = f"SELECT * FROM inventory WHERE sku = '{sku}'"
            rows = self.db.find_by("inventory", "sku", sku)
        else:
            filters = {}
            desc = "SELECT * FROM inventory  -- no filter (full scan)"
            rows = self.db.table("inventory")

        return QueryResult(
            intent=intent,
            query_description=desc,
            table="inventory",
            filters=filters,
            rows=rows,
        )

    def _q_list_users(self, intent: str, ctx: Dict[str, Any]) -> QueryResult:
        """SELECT * FROM users."""
        rows = self.db.table("users")
        return QueryResult(
            intent=intent,
            query_description="SELECT * FROM users",
            table="users",
            filters={},
            rows=rows,
        )

    def _q_list_orders(self, intent: str, ctx: Dict[str, Any]) -> QueryResult:
        """SELECT * FROM orders."""
        rows = self.db.table("orders")
        return QueryResult(
            intent=intent,
            query_description="SELECT * FROM orders",
            table="orders",
            filters={},
            rows=rows,
        )

    def _q_list_inventory(self, intent: str, ctx: Dict[str, Any]) -> QueryResult:
        """SELECT * FROM inventory."""
        rows = self.db.table("inventory")
        return QueryResult(
            intent=intent,
            query_description="SELECT * FROM inventory",
            table="inventory",
            filters={},
            rows=rows,
        )

    def _q_find_test_users(self, intent: str, ctx: Dict[str, Any]) -> QueryResult:
        """SELECT * FROM users WHERE email LIKE '%@test.example.com'."""
        domain = ctx.get("domain", "test.example.com")
        rows = [
            r for r in self.db.table("users")
            if str(r.get("email", "")).endswith(f"@{domain}")
        ]
        return QueryResult(
            intent=intent,
            query_description=f"SELECT * FROM users WHERE email LIKE '%@{domain}'",
            table="users",
            filters={"email_domain": domain},
            rows=rows,
        )

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------

    @staticmethod
    def _log_result(result: QueryResult) -> None:
        """Emit a structured log entry for the query result."""
        if result.success:
            logger.info(
                "[QueryEngine] intent=%r table=%s filters=%s rows=%d",
                result.intent,
                result.table,
                result.filters,
                len(result.rows),
            )
        else:
            logger.warning(
                "[QueryEngine] intent=%r ERROR: %s",
                result.intent,
                result.error,
            )
        # Always print to console so the CLI shows results even without log config
        print("-" * 60)
        print(result)
        print("-" * 60)
