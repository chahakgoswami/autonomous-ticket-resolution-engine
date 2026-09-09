#!/usr/bin/env python3
"""CLI entry point for the Autonomous Ticket Resolution Engine."""

import argparse
import sys

from engine.ticket_reader import TicketReader
from engine.ticket_store import TicketStore
from engine.mock_db import MockDatabase
from engine.query_engine import QueryEngine
from engine.fix_planner import FixPlanner


def cmd_read(args):
    """Read and display a ticket by ID."""
    reader = TicketReader()
    if args.ticket_id == "all":
        tickets = reader.load_all()
        if not tickets:
            print("No tickets found.")
            return
        for t in tickets:
            print("=" * 60)
            print(t)
        print("=" * 60)
        print(f"Total: {len(tickets)} ticket(s)")
    else:
        ticket = reader.load(args.ticket_id)
        if ticket is None:
            print(f"Error: Ticket '{args.ticket_id}' not found.", file=sys.stderr)
            sys.exit(1)
        print(ticket)


def cmd_list(args):
    """List all ticket IDs and titles."""
    store = TicketStore()
    tickets = store.all()
    if not tickets:
        print("No tickets found.")
        return
    print(f"{'ID':<12} {'STATUS':<12} {'PRIORITY':<10} {'TITLE'}")
    print("-" * 70)
    for t in tickets:
        print(f"{t['id']:<12} {t['status']:<12} {t['priority']:<10} {t['title']}")
    print(f"\nTotal: {len(tickets)} ticket(s)")


def cmd_db(args):
    """Inspect a table in the mock database."""
    db = MockDatabase()
    available = db.tables()
    if args.table not in available:
        print(f"Error: Table '{args.table}' not found. Available: {available}", file=sys.stderr)
        sys.exit(1)
    rows = db.table(args.table)
    print(f"Table: {args.table}  ({len(rows)} row(s))")
    print("-" * 60)
    for row in rows:
        print(row)


def cmd_query(args):
    """Run a natural-language-style query against the mock DB."""
    import json

    ctx = {}
    if args.context:
        try:
            ctx = json.loads(args.context)
        except json.JSONDecodeError as exc:
            print(f"Error: --context must be valid JSON. {exc}", file=sys.stderr)
            sys.exit(1)

    engine = QueryEngine()
    result = engine.run(args.intent, ctx)
    if not result.success:
        sys.exit(1)


def cmd_plan(args):
    """Produce and display an ActionPlan for a ticket."""
    reader = TicketReader()
    planner = FixPlanner()

    if args.ticket_id == "all":
        tickets = reader.load_all()
        if not tickets:
            print("No tickets found.")
            return
        for ticket in tickets:
            planner.plan(ticket)
    else:
        ticket = reader.load(args.ticket_id)
        if ticket is None:
            print(f"Error: Ticket '{args.ticket_id}' not found.", file=sys.stderr)
            sys.exit(1)
        planner.plan(ticket)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engine",
        description="Autonomous Ticket Resolution Engine CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # read <ticket_id | all>
    read_p = subparsers.add_parser("read", help="Read a ticket by ID (or 'all')")
    read_p.add_argument("ticket_id", help="Ticket ID (e.g. TKT-001) or 'all'")
    read_p.set_defaults(func=cmd_read)

    # list
    list_p = subparsers.add_parser("list", help="List all tickets")
    list_p.set_defaults(func=cmd_list)

    # db <table>
    db_p = subparsers.add_parser("db", help="Inspect a mock DB table")
    db_p.add_argument("table", help="Table name (users | orders | inventory)")
    db_p.set_defaults(func=cmd_db)

    # query <intent> [--context JSON]
    query_p = subparsers.add_parser(
        "query",
        help="Run a natural-language query against the mock DB",
    )
    query_p.add_argument(
        "intent",
        help="Intent string, e.g. 'find user' or 'check order'",
    )
    query_p.add_argument(
        "--context",
        default="{}",
        metavar="JSON",
        help='JSON object of query parameters, e.g. \'{"email": "john.doe@example.com"}\'',
    )
    query_p.set_defaults(func=cmd_query)

    # plan <ticket_id | all>
    plan_p = subparsers.add_parser(
        "plan",
        help="Produce an ActionPlan for a ticket (or 'all')",
    )
    plan_p.add_argument("ticket_id", help="Ticket ID (e.g. TKT-001) or 'all'")
    plan_p.set_defaults(func=cmd_plan)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
