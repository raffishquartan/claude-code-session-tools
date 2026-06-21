# src/cc_session_tools/cli/ccmsg.py
"""ccmsg — inter-session messaging CLI.

Thin argparse layer over cc_session_tools.lib.messaging.service. Validation
lives here at the boundary; the service trusts its inputs."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools import __version__
from cc_session_tools.lib.messaging import service, store
from cc_session_tools.lib.messaging.addressing import SessionContext
from cc_session_tools.lib.messaging.lock import AlreadyClaimedError
from cc_session_tools.lib.messaging.message import ToKind


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccmsg",
        description="Send and read messages between Claude Code sessions.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    send_p = sub.add_parser("send", help="Compose and route a message.")
    rcpt = send_p.add_argument_group("recipient (exactly one)")
    rcpt.add_argument("--to-session", metavar="UUID")
    rcpt.add_argument("--to-project", metavar="NAME")
    rcpt.add_argument("--to-description", metavar="TEXT")
    send_p.add_argument("--subject", required=True)
    body = send_p.add_mutually_exclusive_group(required=True)
    body.add_argument("--body")
    body.add_argument("--body-file", type=Path)
    send_p.add_argument("--attach", action="append", default=[], metavar="PATH")
    send_p.add_argument("--thread", default=None, metavar="ID")
    # Sender + routing context. All optional: by default the sender uuid comes
    # from $CLAUDE_CODE_SESSION_ID, the display tag from $CLD_SESSION_TAG, and
    # project/partition are derived from the cwd. The flags are overrides (tests
    # and non-Claude-Code callers supply them explicitly).
    send_p.add_argument("--from-project", default=None)
    send_p.add_argument("--from-session", default=None)
    send_p.add_argument("--from-uuid", default=None,
                        help="Sender session uuid (default: $CLAUDE_CODE_SESSION_ID).")
    send_p.add_argument("--from-partition", default=None)
    send_p.add_argument("--to-partition", default=None,
                        help="Override the store partition the message file lives in "
                             "(default: derived from the recipient).")

    read_p = sub.add_parser("read", help="Print one message body and metadata.")
    read_p.add_argument("id")

    list_p = sub.add_parser("list", help="List messages (compact).")
    list_p.add_argument("--status", default=None)
    list_p.add_argument("--partition", default=None)
    list_p.add_argument("--from-uuid", default=None)

    deliver_p = sub.add_parser("deliver", help="Sweep + digest (hook entry).")
    deliver_p.add_argument("--mode", choices=("full", "incremental"), default="full")
    deliver_p.add_argument("--uuid", default=None)
    deliver_p.add_argument("--project", default=None)
    deliver_p.add_argument("--partition", default=None)
    deliver_p.add_argument(
        "--stdin", action="store_true",
        help="Read session context from a hook JSON payload on stdin.",
    )

    claim_p = sub.add_parser("claim", help="Claim a description-addressed message.")
    claim_p.add_argument("id")
    claim_p.add_argument("--uuid", required=True)
    claim_p.add_argument("--session", required=True)

    archive_p = sub.add_parser("archive", help="Manually archive a message.")
    archive_p.add_argument("id")

    return p


def _resolve_recipient(args: argparse.Namespace) -> tuple[ToKind, str]:
    options: list[tuple[ToKind, str | None]] = [
        ("session", args.to_session),
        ("project", args.to_project),
        ("description", args.to_description),
    ]
    chosen = [(kind, val) for kind, val in options if val is not None]
    if len(chosen) != 1:
        raise ValueError(
            "exactly one of --to-session / --to-project / --to-description is required"
        )
    kind, val = chosen[0]
    assert val is not None  # the filter above guarantees this; narrows for mypy
    return kind, val


def _resolve_body(args: argparse.Namespace) -> str:
    raw: str
    if args.body is not None:
        raw = args.body
    else:
        try:
            raw = args.body_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read body file: {exc}") from exc
    if not raw.strip():
        raise ValueError("message body must not be empty")
    return raw


def _validate_attachments(attachments: list[str]) -> None:
    for a in attachments:
        if not Path(a).is_absolute():
            raise ValueError(f"attachment path must be absolute: {a}")


def _resolve_sender(args: argparse.Namespace) -> tuple[str, str, str, str]:
    """Return (from_uuid, from_session, from_project, from_partition).

    Unset flags are filled from the Claude Code environment and the cwd: the
    sender uuid from ``$CLAUDE_CODE_SESSION_ID`` (the same value the statusline/
    hooks receive as ``session_id``), the display tag from ``$CLD_SESSION_TAG``,
    and project/partition from ``partition_for_cwd``."""
    from_uuid = args.from_uuid or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if not from_uuid:
        raise ValueError(
            "cannot determine sender session uuid; pass --from-uuid or run inside "
            "Claude Code (which sets CLAUDE_CODE_SESSION_ID)"
        )
    from_partition = args.from_partition or store.partition_for_cwd(Path.cwd())
    from_project = args.from_project or from_partition.split("/", 1)[-1]
    from_session = args.from_session or os.environ.get("CLD_SESSION_TAG") or from_uuid
    return from_uuid, from_session, from_project, from_partition


def _resolve_to_partition(to_kind: ToKind, to_value: str, override: str | None) -> str:
    """Where the message file is written so the recipient's sweep finds it.

    Project-addressed messages go to that project's partition (sessions there
    sweep it). Session- and description-addressed messages go to ``_global``:
    the sender cannot know a target session's partition, but every session
    sweeps ``_global`` and ``addressing.targets`` matches by uuid / candidate."""
    if override is not None:
        return override
    if to_kind == "project":
        return store.partition_for_project(to_value)
    return store.GLOBAL_PARTITION


def _cmd_send(args: argparse.Namespace) -> int:
    try:
        if not args.subject.strip():
            raise ValueError("subject must not be empty")
        to_kind, to_value = _resolve_recipient(args)
        body = _resolve_body(args)
        _validate_attachments(args.attach)
        from_uuid, from_session, from_project, from_partition = _resolve_sender(args)
        to_partition = _resolve_to_partition(to_kind, to_value, args.to_partition)
    except ValueError as exc:
        print(f"ccmsg: {exc}", file=sys.stderr)
        return 2
    message_id = service.send(service.SendRequest(
        from_project=from_project,
        from_session=from_session,
        from_uuid=from_uuid,
        to_kind=to_kind,
        to_value=to_value,
        to_partition=to_partition,
        subject=args.subject,
        body=body,
        attachments=list(args.attach),
        thread=args.thread,
    ))
    print(message_id)
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    try:
        message = service.read_one(args.id)
    except (ValueError, OSError) as exc:
        print(f"ccmsg: message {args.id} is unreadable: {exc}", file=sys.stderr)
        return 1
    if message is None:
        print(f"ccmsg: message not found: {args.id}", file=sys.stderr)
        return 1
    print(f"id:       {message.id}")
    print(f"from:     {message.from_session} ({message.from_project})")
    print(f"to:       {message.to_kind}={message.to_value}")
    print(f"subject:  {message.subject}")
    print(f"status:   {message.status}")
    print(f"sent_at:  {message.sent_at}")
    if message.attachments:
        print("attach:   " + ", ".join(message.attachments))
    print()
    print(message.body.rstrip())
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = service.list_messages(
        status=args.status,
        partition=args.partition,
        from_uuid=args.from_uuid,
    )
    for r in rows:
        print(f"[{r.id}] {r.status:8} {r.to_kind}={r.to_value} · {r.subject}")
    return 0


def _cmd_deliver(args: argparse.Namespace) -> int:
    if args.stdin:
        data = json.loads(sys.stdin.read())
        uuid = str(data.get("session_id", ""))
        cwd = Path(str(data.get("cwd", Path.cwd())))
        partition = store.partition_for_cwd(cwd)
        project = partition.split("/", 1)[-1]
    else:
        uuid = args.uuid or ""
        partition = args.partition or ""
        project = args.project or ""
    ctx = SessionContext(uuid=uuid, project=project, partition=partition)
    digest = service.deliver(ctx, mode=args.mode)
    if digest:
        print(digest)
    return 0


def _cmd_claim(args: argparse.Namespace) -> int:
    try:
        message = service.claim(
            args.id, service.Claimer(uuid=args.uuid, session=args.session)
        )
    except service.MessageNotFoundError:
        print(f"ccmsg: message not found: {args.id}", file=sys.stderr)
        return 1
    except AlreadyClaimedError:
        print(f"ccmsg: already claimed: {args.id}", file=sys.stderr)
        return 3
    print(f"claimed {message.id}")
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    try:
        service.archive(args.id, datetime.now(timezone.utc))
    except service.MessageNotFoundError:
        print(f"ccmsg: message not found: {args.id}", file=sys.stderr)
        return 1
    except AlreadyClaimedError:
        print(f"ccmsg: message is being claimed, try again: {args.id}", file=sys.stderr)
        return 3
    print(f"archived {args.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "send":
        return _cmd_send(args)
    if args.command == "read":
        return _cmd_read(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "deliver":
        return _cmd_deliver(args)
    if args.command == "claim":
        return _cmd_claim(args)
    if args.command == "archive":
        return _cmd_archive(args)
    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
