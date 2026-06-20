"""Pure, Rich-only renderers for app-generated durable surfaces.

These build durable scrollback blocks for surfaces the app generates outside the
agent event and tool-result streams: the compact startup header, the teleport
outcome line, and the plan-review notice. They take primitives (never app or
Textual state) so they are unit-testable without an app, matching the style of
``tool_result_render`` and ``inline_inject``. The committer enqueues their output
through the single-writer path.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Group, RenderableType
from rich.text import Text

_BRAND = "Usable Vibe"
_REWIND_PREVIEW_MAX = 60


def render_startup_header(*, version: str, model: str, cwd: str) -> RenderableType:
    """Build the compact durable session header committed once at startup.

    Replaces the hidden full ``Banner`` as the durable session context: brand,
    version, and active model on the first line; working directory and the
    ``/help`` hint on the second.
    """
    title = Text.assemble(
        (f"{_BRAND} ", "bold"), (f"v{version}", "dim"), (" · ", "dim"), (model, "cyan")
    )
    meta = Text.assemble(
        (cwd, "dim"),
        (" · ", "dim"),
        ("/help", "cyan"),
        (" for more information", "dim"),
    )
    return Group(title, meta)


def render_teleport_outcome(*, url: str | None, error: str | None) -> RenderableType:
    """Build the durable teleport result line.

    Mirrors ``TeleportMessage.get_content`` for the final state: a green success
    line with the web URL, or a red failure line with the error.
    """
    if error is not None:
        return Text(f"Teleport failed: {error}", style="red")
    return Text(f"Teleported to Vibe Code Web: {url}", style="green")


def render_plan_notice(file_path: Path) -> RenderableType:
    """Build the durable "plan ready for review" notice."""
    return Text.assemble(
        ("📋 ", ""), ("Plan ready for review: ", "bold"), (str(file_path), "cyan")
    )


def render_approval_outcome(
    *, tool_name: str, approved: bool, scope: str | None = None
) -> RenderableType:
    """Build the durable approval decision line.

    The ``ApprovalApp`` form stays live; this records the safety-relevant
    allow/deny outcome once. ``scope`` annotates a persisted allow (e.g.
    "always for this tool").
    """
    if approved:
        line = Text.assemble(
            ("✓ ", "green"), ("Approved ", "green"), (tool_name, "bold")
        )
        if scope:
            line.append(f" ({scope})", style="dim")
        return line
    return Text.assemble(("✗ ", "red"), ("Denied ", "red"), (tool_name, "bold"))


def render_rewind_outcome(
    preview: str, *, restored_files: bool, discarded: int
) -> RenderableType:
    """Build the durable rewind fork marker.

    Native scrollback cannot un-print committed transcript, so a rewind records a
    marker at the fork point instead of erasing prior output: the target message
    preview, how many later messages were discarded, and whether files were
    restored.
    """
    noun = "message" if discarded == 1 else "messages"
    detail = "files restored" if restored_files else "files kept"
    stripped = preview.strip()
    head = stripped.splitlines()[0] if stripped else ""
    if len(head) > _REWIND_PREVIEW_MAX:
        head = head[: _REWIND_PREVIEW_MAX - 1] + "…"
    line = Text.assemble(("↶ ", "magenta"), ("Rewound to: ", "bold"), (head, ""))
    line.append(f"  ({discarded} later {noun} discarded, {detail})", style="dim")
    return line
