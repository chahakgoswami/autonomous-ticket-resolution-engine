"""HumanConfirmationGate: intercepts destructive ActionPlans and requires
explicit human approval before proceeding.  Safe plans are auto-approved.

Public API
----------
    gate = HumanConfirmationGate()
    decision = gate.check(action_plan)          # interactive (reads stdin)
    decision = gate.check(action_plan, auto_yes=True)   # programmatic bypass

Returns a :class:`GateDecision` with .approved (bool) and .reason (str).
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from engine.fix_planner import ActionPlan, ActionType, PlannedAction, RiskLevel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision result
# ---------------------------------------------------------------------------

@dataclass
class GateDecision:
    """Outcome of the HumanConfirmationGate for a single ActionPlan."""

    ticket_id: str
    approved: bool
    reason: str                       # human-readable explanation
    auto_approved: bool = False       # True when no human prompt was needed
    # Snapshot of the plan that was evaluated
    plan_risk: str = ""
    destructive_action_count: int = 0

    def __str__(self) -> str:
        status = "APPROVED" if self.approved else "REJECTED"
        source = "(auto)" if self.auto_approved else "(human)"
        return (
            f"GateDecision [{status}] {source} ticket={self.ticket_id} "
            f"risk={self.plan_risk} destructive_actions={self.destructive_action_count} "
            f"reason={self.reason!r}"
        )


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

class HumanConfirmationGate:
    """Intercept destructive ActionPlans and obtain explicit human approval.

    Parameters
    ----------
    auto_approve_safe:
        When *True* (default), safe (read-only) plans skip the prompt.
    prompt_stream:
        A file-like object used to write the prompt.  Defaults to stdout.
        Useful for testing.
    input_fn:
        Callable that reads a line of user input.  Defaults to the built-in
        ``input()``.  Replace with a lambda for programmatic testing.
    """

    def __init__(
        self,
        auto_approve_safe: bool = True,
        prompt_stream=None,
        input_fn=None,
    ):
        import sys
        self.auto_approve_safe = auto_approve_safe
        self._stream = prompt_stream or sys.stdout
        self._input_fn = input_fn or input

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        plan: ActionPlan,
        auto_yes: bool = False,
    ) -> GateDecision:
        """Evaluate *plan* and return a :class:`GateDecision`.

        Args:
            plan:     The ActionPlan produced by FixPlanner.
            auto_yes: If *True*, bypass the interactive prompt and approve
                      automatically (useful for programmatic / batch runs
                      where a higher-level caller has already confirmed).
        """
        destructive_actions = [
            a for a in plan.actions if a.risk_level == RiskLevel.DESTRUCTIVE
        ]
        n_destructive = len(destructive_actions)

        # --- Safe plan: auto-approve without prompting -------------------
        if plan.is_safe and self.auto_approve_safe:
            decision = GateDecision(
                ticket_id=plan.ticket_id,
                approved=True,
                reason="Safe plan — auto-approved without human prompt.",
                auto_approved=True,
                plan_risk=plan.overall_risk.value,
                destructive_action_count=0,
            )
            self._log(decision)
            return decision

        # --- Destructive plan: display summary and prompt ----------------
        self._print_header(plan)
        self._print_diff(plan, destructive_actions)

        if auto_yes:
            approved = True
            reason = "Destructive plan auto-approved via auto_yes flag."
            auto_approved = True
        else:
            approved, reason = self._prompt_user(plan)
            auto_approved = False

        decision = GateDecision(
            ticket_id=plan.ticket_id,
            approved=approved,
            reason=reason,
            auto_approved=auto_approved,
            plan_risk=plan.overall_risk.value,
            destructive_action_count=n_destructive,
        )
        self._log(decision)
        return decision

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def _print_header(self, plan: ActionPlan) -> None:
        """Print a prominent header for the destructive plan."""
        w = 70
        self._write("\n" + "!" * w)
        self._write(f"  DESTRUCTIVE ACTION REQUIRED — HUMAN APPROVAL NEEDED")
        self._write("!" * w)
        self._write(f"  Ticket  : {plan.ticket_id}  —  {plan.ticket_title}")
        self._write(f"  Risk    : {plan.overall_risk.value.upper()}")
        self._write(f"  Actions : {len(plan.actions)} total  "
                    f"({sum(1 for a in plan.actions if a.risk_level == RiskLevel.DESTRUCTIVE)} destructive)")
        if plan.notes:
            self._write(f"  Notes   : {plan.notes}")
        self._write("!" * w + "\n")

    def _print_diff(self, plan: ActionPlan, destructive_actions: list) -> None:
        """Print a diff-style summary showing exactly what will change."""
        self._write("┌─ ACTION PLAN SUMMARY " + "─" * 48 + "┐")
        self._write(f"│  Ticket: {plan.ticket_id:<58}│")
        self._write(f"│  Total actions: {len(plan.actions):<52}│")
        self._write("├─────────────────────────────────────────────────────────────────────┤")

        for i, action in enumerate(plan.actions, 1):
            risk_tag = "[!]" if action.risk_level == RiskLevel.DESTRUCTIVE else "[ ]"
            self._write(f"│  Step {i}: {risk_tag} {action.action_type.value.upper():<60}│"[: 72] + "│")

        self._write("├─ DESTRUCTIVE CHANGES (diff) " + "─" * 41 + "┤")

        if not destructive_actions:
            self._write("│  (none)" + " " * 62 + "│")
        else:
            for action in destructive_actions:
                self._write("│" + " " * 70 + "│")
                self._write(f"│  - Action  : {action.action_type.value.upper()}" + " " * 50 + "│")
                # Description (may be long — wrap at 66 chars)
                desc = action.description
                for chunk in self._wrap(desc, width=64):
                    self._write(f"│    {chunk:<66}│")
                # Show what currently exists vs what will change
                self._write(f"│    Table   : {action.target_table:<56}│")
                if action.target_field and action.target_value is not None:
                    fv = f"{action.target_field} = {action.target_value!r}"
                    self._write(f"│    Filter  : {fv:<56}│")
                if action.new_value is not None:
                    # Show each key as a - / + diff line
                    if isinstance(action.new_value, dict):
                        for k, v in action.new_value.items():
                            self._write(f"│    - old {k}: (current value in DB)" + " " * 28 + "│")
                            self._write(f"│    + new {k}: {str(v):<52}│")
                    else:
                        self._write(f"│    + new value: {str(action.new_value):<52}│")
                if action.params:
                    for pk, pv in action.params.items():
                        self._write(f"│    param {pk}: {str(pv):<53}│")

        self._write("└" + "─" * 70 + "┘\n")

    # ------------------------------------------------------------------
    # User prompt
    # ------------------------------------------------------------------

    def _prompt_user(self, plan: ActionPlan):
        """Ask the operator for yes/no approval.  Returns (approved, reason)."""
        self._write("Do you approve these destructive changes?")
        self._write("  Type  'yes'  to APPROVE and continue execution.")
        self._write("  Type  'no'   to REJECT and block this ticket.")
        self._write("")

        for attempt in range(3):
            try:
                raw = self._input_fn(f"  Approval for {plan.ticket_id} [yes/no]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False, "Input interrupted — plan rejected for safety."

            if raw in ("yes", "y"):
                return True, "Operator approved via interactive prompt."
            if raw in ("no", "n"):
                return False, "Operator rejected via interactive prompt."

            self._write(f"  Unrecognised input '{raw}'. Please type 'yes' or 'no'.")

        # Fail safe: reject after 3 bad attempts
        return False, "No valid response after 3 attempts — plan rejected for safety."

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    def _write(self, text: str) -> None:
        """Write a line to the prompt stream."""
        print(text, file=self._stream)

    @staticmethod
    def _wrap(text: str, width: int = 64):
        """Simple word-wrap yielding chunks of at most *width* chars."""
        words = text.split()
        line = ""
        for word in words:
            if len(line) + len(word) + 1 <= width:
                line = f"{line} {word}".strip()
            else:
                if line:
                    yield line
                line = word
        if line:
            yield line

    @staticmethod
    def _log(decision: GateDecision) -> None:
        """Emit a structured log line."""
        if decision.approved:
            logger.info(
                "[ConfirmationGate] APPROVED ticket=%s auto=%s reason=%r",
                decision.ticket_id,
                decision.auto_approved,
                decision.reason,
            )
        else:
            logger.warning(
                "[ConfirmationGate] REJECTED ticket=%s reason=%r",
                decision.ticket_id,
                decision.reason,
            )
        print(decision)
