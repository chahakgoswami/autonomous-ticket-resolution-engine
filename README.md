# Autonomous Ticket Resolution Engine

Autonomous agent that reads tickets, queries a mock DB, applies fixes, and confirms destructive acti

**Domain:** Agentic AI
**Language:** python


## 7-day build plan

- [ ] Day 1: Scaffold the project structure with a mock ticket store (JSON-backed), a mock database layer with sample records, and a basic TicketReader class that loads and parses tickets by ID with a simple CLI entry point.
- [ ] Day 2: Build the QueryEngine that accepts natural-language-style ticket intent (e.g. 'find user', 'check order') and maps it to mock SQL queries against the in-memory database, returning structured results logged to console.
- [ ] Day 3: Implement the FixPlanner that classifies each ticket into read-only actions (safe) vs destructive actions (delete, update, reset) and produces an ActionPlan object describing what steps the agent will take before executing anything.
- [ ] Day 4: Add the HumanConfirmationGate — a terminal prompt that intercepts any destructive ActionPlan, displays a diff-style summary of changes, and requires explicit yes/no approval before proceeding, with auto-approve for safe actions.
- [ ] Day 5: Wire up the ResolutionExecutor that applies approved ActionPlans against the mock database, updates ticket status to 'resolved' or 'blocked', and emits a structured resolution report per ticket including before/after state.
- [ ] Day 6: Integrate all components into an AutonomousAgent orchestrator with a run-loop that processes a batch of tickets sequentially, handles errors gracefully per ticket without halting the batch, and writes a JSON audit log of every decision and action.
- [ ] Day 7: Add a pytest test suite covering the QueryEngine mapping, FixPlanner classification, HumanConfirmationGate bypass logic, ResolutionExecutor state changes, and a full end-to-end integration test running the agent against a fixture ticket batch with simulated approvals.

