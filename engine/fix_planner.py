"""FixPlanner: classifies tickets and produces ActionPlan objects.

Each ticket is analysed for intent keywords.  Actions are classified as
either SAFE (read-only) or DESTRUCTIVE (delete / update / reset).  The
resulting ActionPlan describes every step the agent intends to take
*before* any execution occurs.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from engine.ticket_reader import Ticket

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    """Broad category for a single planned action."""
    # Read-only / safe
    QUERY = "query"                  # look up data
    REPORT = "report"                # generate a report
    # Destructive
    UPDATE = "update"                # modify a field on a record
    DELETE = "delete"                # remove one or more records
    RESET = "reset"                  # reset a value (password, counter, …)
    UNLOCK = "unlock"                # unlock an account (treated as UPDATE)


class RiskLevel(str, Enum):
    SAFE = "safe"                    # read-only, no side-effects
    DESTRUCTIVE = "destructive"      # mutates or deletes data


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PlannedAction:
    """A single step inside an ActionPlan."""
    action_type: ActionType
    risk_level: RiskLevel
    description: str
    target_table: str
    target_field: Optional[str] = None
    target_value: Optional[Any] = None
    new_value: Optional[Any] = None
    # Extra free-form params for complex actions (e.g. domain pattern)
    params: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        parts = [
            f"  [{self.risk_level.value.upper()}] {self.action_type.value.upper()}",
            f"    Description : {self.description}",
            f"    Table       : {self.target_table}",
        ]
        if self.target_field:
            parts.append(f"    Filter      : {self.target_field} = {self.target_value!r}")
        if self.new_value is not None:
            parts.append(f"    New value   : {self.new_value!r}")
        if self.params:
            parts.append(f"    Params      : {self.params}")
        return "\n".join(parts)


@dataclass
class ActionPlan:
    """Complete plan for resolving a single ticket."""
    ticket_id: str
    ticket_title: str
    overall_risk: RiskLevel          # DESTRUCTIVE if *any* action is destructive
    actions: List[PlannedAction] = field(default_factory=list)
    query_intent: str = ""           # intent string for the QueryEngine
    query_context: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""                  # human-readable planner notes

    @property
    def is_destructive(self) -> bool:
        return self.overall_risk == RiskLevel.DESTRUCTIVE

    @property
    def is_safe(self) -> bool:
        return self.overall_risk == RiskLevel.SAFE

    def __str__(self) -> str:
        lines = [
            f"ActionPlan for {self.ticket_id}: {self.ticket_title}",
            f"Overall risk : {self.overall_risk.value.upper()}",
            f"Query intent : {self.query_intent or '(none)'}",
        ]
        if self.query_context:
            lines.append(f"Query context: {self.query_context}")
        if self.notes:
            lines.append(f"Notes        : {self.notes}")
        lines.append(f"Actions ({len(self.actions)}):")
        for action in self.actions:
            lines.append(str(action))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# FixPlanner
# ---------------------------------------------------------------------------

class FixPlanner:
    """Classifies a Ticket and produces an ActionPlan.

    Classification strategy
    -----------------------
    The planner inspects the ticket's *category*, *title*, *description*,
    and *metadata* for keyword signals.  It follows a simple rule-table:

    Category / keyword          → actions produced
    -------------------------------------------------
    account + locked/lock       → QUERY user, UNLOCK user (destructive)
    account + password/reset    → QUERY user, RESET password (destructive)
    order / order_id present    → QUERY order (safe)
    inventory / sku present     → QUERY inventory (safe)
    database + delete/remove    → QUERY records, DELETE records (destructive)
    (fallback)                  → QUERY description keywords (safe)
    """

    # Keywords that indicate a destructive intent in title+description
    _DESTRUCTIVE_KEYWORDS = {
        "delete", "remove", "drop", "purge",
        "update", "modify", "change", "edit",
        "reset", "clear",
        "unlock", "unblock",
    }

    def plan(self, ticket: Ticket) -> ActionPlan:
        """Return an :class:`ActionPlan` for the given ticket."""
        category = ticket.category.lower()
        text = f"{ticket.title} {ticket.description}".lower()
        meta = ticket.metadata

        # Delegate to specialised planners
        if category == "account":
            plan = self._plan_account(ticket, text, meta)
        elif category == "order" or "order_id" in meta:
            plan = self._plan_order(ticket, text, meta)
        elif category == "inventory" or "sku" in meta:
            plan = self._plan_inventory(ticket, text, meta)
        elif category == "database":
            plan = self._plan_database(ticket, text, meta)
        else:
            plan = self._plan_fallback(ticket, text, meta)

        # Recompute overall_risk from the actions list
        plan.overall_risk = self._compute_risk(plan.actions)

        logger.info(
            "[FixPlanner] ticket=%s risk=%s actions=%d",
            ticket.id,
            plan.overall_risk.value,
            len(plan.actions),
        )
        print("=" * 60)
        print(plan)
        print("=" * 60)
        return plan

    # ------------------------------------------------------------------
    # Category-specific planners
    # ------------------------------------------------------------------

    def _plan_account(self, ticket: Ticket, text: str, meta: dict) -> ActionPlan:
        actions: List[PlannedAction] = []
        user_email = meta.get("user_email") or meta.get("email")
        query_ctx = {}
        if user_email:
            query_ctx["email"] = user_email

        # Always query the user first
        actions.append(PlannedAction(
            action_type=ActionType.QUERY,
            risk_level=RiskLevel.SAFE,
            description=f"Look up user account: {user_email or '(unknown)'}",
            target_table="users",
            target_field="email" if user_email else None,
            target_value=user_email,
        ))

        notes_parts = []

        if any(kw in text for kw in ("locked", "lock", "failed login", "failed attempts")):
            actions.append(PlannedAction(
                action_type=ActionType.UNLOCK,
                risk_level=RiskLevel.DESTRUCTIVE,
                description=f"Unlock account and reset failed_login_attempts to 0 for {user_email}",
                target_table="users",
                target_field="email",
                target_value=user_email,
                new_value={"status": "active", "failed_login_attempts": 0},
            ))
            notes_parts.append("Account unlock required — destructive UPDATE.")

        if any(kw in text for kw in ("reset", "password", "forgot", "forgotten")):
            actions.append(PlannedAction(
                action_type=ActionType.RESET,
                risk_level=RiskLevel.DESTRUCTIVE,
                description=f"Reset password for user {user_email}",
                target_table="users",
                target_field="email",
                target_value=user_email,
                new_value={"password_hash": "<NEW_TEMP_HASH>", "force_password_change": True},
            ))
            notes_parts.append("Password reset required — destructive UPDATE.")

        # If only the QUERY action was added, that's a safe-only plan
        if len(actions) == 1:
            notes_parts.append("Read-only inspection; no mutations planned.")

        return ActionPlan(
            ticket_id=ticket.id,
            ticket_title=ticket.title,
            overall_risk=RiskLevel.SAFE,  # recomputed by caller
            actions=actions,
            query_intent="find user",
            query_context=query_ctx,
            notes=" ".join(notes_parts),
        )

    def _plan_order(self, ticket: Ticket, text: str, meta: dict) -> ActionPlan:
        actions: List[PlannedAction] = []
        order_id = meta.get("order_id")
        customer_email = meta.get("customer_email")
        query_ctx = {}
        if order_id:
            query_ctx["order_id"] = order_id
        if customer_email:
            query_ctx["customer_email"] = customer_email

        actions.append(PlannedAction(
            action_type=ActionType.QUERY,
            risk_level=RiskLevel.SAFE,
            description=f"Look up order {order_id or '(unknown)'}",
            target_table="orders",
            target_field="id" if order_id else None,
            target_value=order_id,
        ))

        notes = "Read-only order inspection."

        # Check if a status update is needed (re-trigger / update mentioned)
        if any(kw in text for kw in ("re-trigger", "retrigger", "update", "fix", "stuck", "reset")):
            actions.append(PlannedAction(
                action_type=ActionType.UPDATE,
                risk_level=RiskLevel.DESTRUCTIVE,
                description=f"Update order {order_id} status to re-trigger fulfillment",
                target_table="orders",
                target_field="id",
                target_value=order_id,
                new_value={"status": "pending_fulfillment"},
            ))
            notes = "Order status update required — destructive UPDATE."

        return ActionPlan(
            ticket_id=ticket.id,
            ticket_title=ticket.title,
            overall_risk=RiskLevel.SAFE,
            actions=actions,
            query_intent="find order",
            query_context=query_ctx,
            notes=notes,
        )

    def _plan_inventory(self, ticket: Ticket, text: str, meta: dict) -> ActionPlan:
        sku = meta.get("sku")
        query_ctx = {"sku": sku} if sku else {}

        actions = [
            PlannedAction(
                action_type=ActionType.QUERY,
                risk_level=RiskLevel.SAFE,
                description=f"Check inventory levels for {sku or '(all SKUs)'}",
                target_table="inventory",
                target_field="sku" if sku else None,
                target_value=sku,
            ),
            PlannedAction(
                action_type=ActionType.REPORT,
                risk_level=RiskLevel.SAFE,
                description="Generate inventory status report",
                target_table="inventory",
            ),
        ]

        return ActionPlan(
            ticket_id=ticket.id,
            ticket_title=ticket.title,
            overall_risk=RiskLevel.SAFE,
            actions=actions,
            query_intent="check inventory",
            query_context=query_ctx,
            notes="Read-only inventory check.",
        )

    def _plan_database(self, ticket: Ticket, text: str, meta: dict) -> ActionPlan:
        actions: List[PlannedAction] = []
        affected_table = meta.get("affected_table", "users")
        pattern = meta.get("pattern", "")
        domain = "test.example.com"
        if "@" in pattern:
            domain = pattern.split("@")[-1]

        query_ctx = {"domain": domain}

        actions.append(PlannedAction(
            action_type=ActionType.QUERY,
            risk_level=RiskLevel.SAFE,
            description=f"Find records matching pattern '{pattern}' in {affected_table}",
            target_table=affected_table,
            params={"domain": domain},
        ))

        if any(kw in text for kw in ("delete", "remove", "purge", "drop")):
            actions.append(PlannedAction(
                action_type=ActionType.DELETE,
                risk_level=RiskLevel.DESTRUCTIVE,
                description=f"Delete all records matching '{pattern}' from {affected_table}",
                target_table=affected_table,
                params={"domain": domain, "pattern": pattern},
            ))

        return ActionPlan(
            ticket_id=ticket.id,
            ticket_title=ticket.title,
            overall_risk=RiskLevel.SAFE,
            actions=actions,
            query_intent="find test users",
            query_context=query_ctx,
            notes="Destructive DELETE of test records pending human approval.",
        )

    def _plan_fallback(self, ticket: Ticket, text: str, meta: dict) -> ActionPlan:
        """Generic fallback: produce a QUERY action and flag if text looks destructive."""
        is_destructive = any(kw in text for kw in self._DESTRUCTIVE_KEYWORDS)
        query_intent = self._guess_intent(text)

        actions: List[PlannedAction] = [
            PlannedAction(
                action_type=ActionType.QUERY,
                risk_level=RiskLevel.SAFE,
                description="Inspect ticket data (generic fallback query)",
                target_table="(derived at runtime)",
                params={"raw_text_snippet": text[:120]},
            )
        ]

        notes = "Generic fallback plan."
        if is_destructive:
            actions.append(PlannedAction(
                action_type=ActionType.UPDATE,
                risk_level=RiskLevel.DESTRUCTIVE,
                description="Potential destructive action detected from ticket text",
                target_table="(derived at runtime)",
                params={"detected_keywords": [
                    kw for kw in self._DESTRUCTIVE_KEYWORDS if kw in text
                ]},
            ))
            notes += " Destructive keywords detected — manual review advised."

        return ActionPlan(
            ticket_id=ticket.id,
            ticket_title=ticket.title,
            overall_risk=RiskLevel.SAFE,
            actions=actions,
            query_intent=query_intent,
            query_context={},
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_risk(actions: List[PlannedAction]) -> RiskLevel:
        """Return DESTRUCTIVE if any action is destructive, else SAFE."""
        for action in actions:
            if action.risk_level == RiskLevel.DESTRUCTIVE:
                return RiskLevel.DESTRUCTIVE
        return RiskLevel.SAFE

    @staticmethod
    def _guess_intent(text: str) -> str:
        """Best-effort intent string for the QueryEngine from raw text."""
        if "user" in text:
            return "find user"
        if "order" in text:
            return "find order"
        if "inventory" in text or "stock" in text or "sku" in text:
            return "check inventory"
        return "list users"
