"""ResolutionExecutor: applies approved ActionPlans against the mock database,
updates ticket status to 'resolved' or 'blocked', and emits structured
resolution reports including before/after state.

Public API
----------
    executor = ResolutionExecutor(db, ticket_store)
    report   = executor.execute(plan, gate_decision)

Returns a :class:`ResolutionReport` with full before/after snapshots.
"""

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engine.confirmation_gate import GateDecision
from engine.fix_planner import ActionPlan, ActionType, PlannedAction, RiskLevel
from engine.mock_db import MockDatabase
from engine.ticket_store import TicketStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """Outcome of executing a single PlannedAction."""
    action_type: str
    description: str
    target_table: str
    success: bool
    rows_affected: int = 0
    before_state: Optional[List[dict]] = None
    after_state: Optional[List[dict]] = None
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: str = ""

    def summary(self) -> str:
        if self.skipped:
            return f"[SKIPPED] {self.action_type} on {self.target_table}: {self.skip_reason}"
        if not self.success:
            return f"[ERROR]   {self.action_type} on {self.target_table}: {self.error}"
        return (
            f"[OK]      {self.action_type} on {self.target_table} "
            f"— {self.rows_affected} row(s) affected"
        )


@dataclass
class ResolutionReport:
    """Full resolution report for a single ticket."""
    ticket_id: str
    ticket_title: str
    ticket_status_before: str
    ticket_status_after: str
    approved: bool
    overall_success: bool
    action_results: List[ActionResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    executed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    notes: str = ""

    # ------------------------------------------------------------------ #

    def __str__(self) -> str:
        lines = [
            "=" * 70,
            f"  RESOLUTION REPORT  —  {self.ticket_id}",
            "=" * 70,
            f"  Title          : {self.ticket_title}",
            f"  Executed at    : {self.executed_at}",
            f"  Approved       : {'YES' if self.approved else 'NO'}",
            f"  Overall status : {'SUCCESS' if self.overall_success else 'FAILURE/BLOCKED'}",
            f"  Ticket status  : {self.ticket_status_before!r}  →  {self.ticket_status_after!r}",
        ]
        if self.notes:
            lines.append(f"  Notes          : {self.notes}")

        lines.append("\n  Action Results:")
        for i, ar in enumerate(self.action_results, 1):
            lines.append(f"    {i}. {ar.summary()}")
            if ar.before_state is not None:
                lines.append(f"       Before : {ar.before_state}")
            if ar.after_state is not None:
                lines.append(f"       After  : {ar.after_state}")
            if ar.error:
                lines.append(f"       Error  : {ar.error}")

        if self.errors:
            lines.append("\n  Errors:")
            for err in self.errors:
                lines.append(f"    - {err}")

        lines.append("=" * 70)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict of the report."""
        return {
            "ticket_id": self.ticket_id,
            "ticket_title": self.ticket_title,
            "ticket_status_before": self.ticket_status_before,
            "ticket_status_after": self.ticket_status_after,
            "approved": self.approved,
            "overall_success": self.overall_success,
            "executed_at": self.executed_at,
            "notes": self.notes,
            "errors": self.errors,
            "action_results": [
                {
                    "action_type": ar.action_type,
                    "description": ar.description,
                    "target_table": ar.target_table,
                    "success": ar.success,
                    "rows_affected": ar.rows_affected,
                    "skipped": ar.skipped,
                    "skip_reason": ar.skip_reason,
                    "before_state": ar.before_state,
                    "after_state": ar.after_state,
                    "error": ar.error,
                }
                for ar in self.action_results
            ],
        }


# ---------------------------------------------------------------------------
# ResolutionExecutor
# ---------------------------------------------------------------------------

class ResolutionExecutor:
    """Applies approved ActionPlans to the mock database and emits reports.

    Parameters
    ----------
    db:
        :class:`~engine.mock_db.MockDatabase` instance (in-memory mutations only).
    ticket_store:
        :class:`~engine.ticket_store.TicketStore` instance used to update
        ticket status after execution.
    """

    def __init__(
        self,
        db: Optional[MockDatabase] = None,
        ticket_store: Optional[TicketStore] = None,
    ):
        self.db = db or MockDatabase()
        self.ticket_store = ticket_store or TicketStore()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, plan: ActionPlan, decision: GateDecision) -> ResolutionReport:
        """Execute *plan* if *decision* is approved.

        If the gate rejected the plan, the ticket is marked 'blocked' and
        a report with no executed actions is returned.

        Args:
            plan:     The :class:`ActionPlan` from the FixPlanner.
            decision: The :class:`GateDecision` from the HumanConfirmationGate.

        Returns:
            A :class:`ResolutionReport` with full before/after state.
        """
        # Snapshot the ticket's current status from the store
        ticket_raw = self.ticket_store.get(plan.ticket_id)
        status_before = ticket_raw["status"] if ticket_raw else "unknown"

        # --- Rejected: mark blocked and return immediately ---------------
        if not decision.approved:
            self.ticket_store.update_status(plan.ticket_id, "blocked")
            report = ResolutionReport(
                ticket_id=plan.ticket_id,
                ticket_title=plan.ticket_title,
                ticket_status_before=status_before,
                ticket_status_after="blocked",
                approved=False,
                overall_success=False,
                notes=f"Plan rejected by gate: {decision.reason}",
            )
            logger.warning(
                "[ResolutionExecutor] ticket=%s BLOCKED reason=%r",
                plan.ticket_id,
                decision.reason,
            )
            print(report)
            return report

        # --- Approved: execute each action --------------------------------
        action_results: List[ActionResult] = []
        global_errors: List[str] = []
        all_ok = True

        for action in plan.actions:
            result = self._execute_action(action)
            action_results.append(result)
            if result.skipped:
                continue
            if not result.success:
                all_ok = False
                global_errors.append(
                    f"{action.action_type.value} on {action.target_table}: {result.error}"
                )

        # Update ticket status
        new_ticket_status = "resolved" if all_ok else "blocked"
        self.ticket_store.update_status(plan.ticket_id, new_ticket_status)

        report = ResolutionReport(
            ticket_id=plan.ticket_id,
            ticket_title=plan.ticket_title,
            ticket_status_before=status_before,
            ticket_status_after=new_ticket_status,
            approved=True,
            overall_success=all_ok,
            action_results=action_results,
            errors=global_errors,
            notes=plan.notes,
        )

        logger.info(
            "[ResolutionExecutor] ticket=%s status=%s actions=%d errors=%d",
            plan.ticket_id,
            new_ticket_status,
            len(action_results),
            len(global_errors),
        )
        print(report)
        return report

    # ------------------------------------------------------------------
    # Action dispatching
    # ------------------------------------------------------------------

    def _execute_action(self, action: PlannedAction) -> ActionResult:
        """Dispatch a single PlannedAction to the appropriate handler."""
        try:
            if action.action_type in (ActionType.QUERY, ActionType.REPORT):
                return self._exec_query(action)
            elif action.action_type in (ActionType.UPDATE, ActionType.UNLOCK):
                return self._exec_update(action)
            elif action.action_type == ActionType.RESET:
                return self._exec_reset(action)
            elif action.action_type == ActionType.DELETE:
                return self._exec_delete(action)
            else:
                return ActionResult(
                    action_type=action.action_type.value,
                    description=action.description,
                    target_table=action.target_table,
                    success=False,
                    error=f"Unknown action type: {action.action_type}",
                )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception(
                "[ResolutionExecutor] Unhandled error executing action %s on %s",
                action.action_type,
                action.target_table,
            )
            return ActionResult(
                action_type=action.action_type.value,
                description=action.description,
                target_table=action.target_table,
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Individual action handlers
    # ------------------------------------------------------------------

    def _exec_query(self, action: PlannedAction) -> ActionResult:
        """Execute a QUERY or REPORT action (read-only)."""
        table = action.target_table

        # Tables that start with '(' are placeholders from the fallback planner
        if table.startswith("("):
            return ActionResult(
                action_type=action.action_type.value,
                description=action.description,
                target_table=table,
                success=True,
                skipped=True,
                skip_reason="Target table not yet known (fallback plan); skipping read.",
            )

        try:
            if action.target_field and action.target_value is not None:
                rows = self.db.find_by(table, action.target_field, action.target_value)
            elif action.params.get("domain"):
                domain = action.params["domain"]
                rows = [
                    r for r in self.db.table(table)
                    if str(r.get("email", "")).endswith(f"@{domain}")
                ]
            else:
                rows = self.db.table(table)
        except KeyError as exc:
            return ActionResult(
                action_type=action.action_type.value,
                description=action.description,
                target_table=table,
                success=False,
                error=str(exc),
            )

        return ActionResult(
            action_type=action.action_type.value,
            description=action.description,
            target_table=table,
            success=True,
            rows_affected=len(rows),
            before_state=copy.deepcopy(rows),
            after_state=copy.deepcopy(rows),  # read-only: same before/after
        )

    def _exec_update(self, action: PlannedAction) -> ActionResult:
        """Execute an UPDATE or UNLOCK action."""
        table = action.target_table
        if table.startswith("("):
            return ActionResult(
                action_type=action.action_type.value,
                description=action.description,
                target_table=table,
                success=True,
                skipped=True,
                skip_reason="Target table not yet known (fallback plan); skipping update.",
            )

        if not action.target_field or action.target_value is None:
            return ActionResult(
                action_type=action.action_type.value,
                description=action.description,
                target_table=table,
                success=False,
                error="UPDATE action missing target_field or target_value.",
            )

        updates: Dict[str, Any] = {}
        if isinstance(action.new_value, dict):
            updates = action.new_value
        elif action.new_value is not None:
            updates = {action.target_field: action.new_value}

        if not updates:
            return ActionResult(
                action_type=action.action_type.value,
                description=action.description,
                target_table=table,
                success=False,
                error="UPDATE action has no new_value to apply.",
            )

        # Snapshot before
        before = copy.deepcopy(
            self.db.find_by(table, action.target_field, action.target_value)
        )

        count = self.db.update_row(
            table,
            action.target_field,
            action.target_value,
            updates,
        )

        # Snapshot after
        after = copy.deepcopy(
            self.db.find_by(table, action.target_field, action.target_value)
        )

        return ActionResult(
            action_type=action.action_type.value,
            description=action.description,
            target_table=table,
            success=True,
            rows_affected=count,
            before_state=before,
            after_state=after,
        )

    def _exec_reset(self, action: PlannedAction) -> ActionResult:
        """Execute a RESET action (treated as a targeted UPDATE)."""
        # Re-use the UPDATE handler — same mechanics
        return self._exec_update(action)

    def _exec_delete(self, action: PlannedAction) -> ActionResult:
        """Execute a DELETE action."""
        table = action.target_table

        domain = action.params.get("domain")
        pattern = action.params.get("pattern", "")

        if domain:
            # Snapshot matching rows before deletion
            before = copy.deepcopy([
                r for r in self.db.table(table)
                if str(r.get("email", "")).endswith(f"@{domain}")
            ])
            count = self.db.delete_rows_where(
                table,
                lambda r: str(r.get("email", "")).endswith(f"@{domain}"),
            )
            after: List[dict] = []  # all matching rows deleted
            return ActionResult(
                action_type=action.action_type.value,
                description=action.description,
                target_table=table,
                success=True,
                rows_affected=count,
                before_state=before,
                after_state=after,
            )

        if action.target_field and action.target_value is not None:
            before = copy.deepcopy(
                self.db.find_by(table, action.target_field, action.target_value)
            )
            count = self.db.delete_rows(table, action.target_field, action.target_value)
            return ActionResult(
                action_type=action.action_type.value,
                description=action.description,
                target_table=table,
                success=True,
                rows_affected=count,
                before_state=before,
                after_state=[],
            )

        return ActionResult(
            action_type=action.action_type.value,
            description=action.description,
            target_table=table,
            success=False,
            error="DELETE action requires either params.domain or target_field+target_value.",
        )
