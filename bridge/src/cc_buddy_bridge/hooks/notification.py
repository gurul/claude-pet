"""Notification hook — Claude needs something from the user.

Claude Code fires Notification when it needs permission for a tool call or
has been waiting on user input. Forward it so the buddy goes into its
attention animation until the user responds (next UserPromptSubmit /
PostToolUse clears it).
"""

from __future__ import annotations

from ._client import post, read_hook_input


def main() -> int:
    payload = read_hook_input()
    post({
        "evt": "notification",
        "session_id": payload.get("session_id", ""),
        "message": payload.get("message", ""),
        "notification_type": payload.get("type") or payload.get("notification_type") or "",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
