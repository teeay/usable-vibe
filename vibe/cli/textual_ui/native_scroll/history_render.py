"""Semantic renderers for non-local transcript sources (resume / history).

Resumed session history arrives as :class:`LLMMessage` records, not as
``BaseEvent`` values or typed tool-result models. These pure renderers map each
record to a Rich block at the same fidelity the upstream full-screen path uses
for resumed history (``build_history_widgets``): user prompts, assistant
Markdown, tool-call lines, and tool-result content rendered from the stored
content string. They never scrape a widget and never mount into hidden
``#messages``; the committer enqueues the blocks into native scrollback.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text

from vibe.cli.textual_ui.native_scroll.tool_result_render import shorten_text_middle
from vibe.cli.textual_ui.native_scroll.widget_render import render_user_prompt
from vibe.cli.textual_ui.widgets.messages import UserMessage
from vibe.core.types import LLMMessage, Role

_SHORTENABLE_TOOL_NAMES = {"bash", "read", "grep"}

HistoryBlock = tuple[RenderableType, bool]


def render_history_blocks(
    messages: Sequence[LLMMessage],
    tool_call_map: dict[str, str],
    *,
    omitted_count: int,
    shorten_tool_output: bool = True,
    tool_output_head_lines: int = 3,
    tool_output_tail_lines: int = 3,
    dark: bool = True,
) -> list[HistoryBlock]:
    """Render resumed history messages to durable scrollback blocks.

    ``omitted_count`` is the number of earlier (backfill) messages dropped before
    the committed tail; when non-zero a leading marker records that they exist,
    since native mode does not offer the interactive load-more affordance.
    """
    blocks: list[HistoryBlock] = []
    if omitted_count > 0:
        noun = "message" if omitted_count == 1 else "messages"
        blocks.append((
            Text(f"↑ {omitted_count} earlier {noun} omitted", style="dim italic"),
            False,
        ))

    for msg in messages:
        if msg.injected:
            continue
        match msg.role:
            case Role.user:
                if msg.content or msg.images:
                    blocks.append((
                        render_user_prompt(
                            UserMessage.PROMPT_CHAR,
                            msg.content or "",
                            msg.images,
                            dark=dark,
                        ),
                        True,
                    ))
            case Role.assistant:
                if msg.content:
                    blocks.append((Markdown(msg.content), True))
                for tool_call in msg.tool_calls or []:
                    name = tool_call.function.name or "unknown"
                    blocks.append((
                        Text.assemble(("⚙ ", "cyan"), (name, "bold")),
                        False,
                    ))
            case Role.tool:
                name = msg.name or tool_call_map.get(msg.tool_call_id or "", "tool")
                if msg.content:
                    content = msg.content
                    if shorten_tool_output and name in _SHORTENABLE_TOOL_NAMES:
                        content = shorten_text_middle(
                            content,
                            head_lines=tool_output_head_lines,
                            tail_lines=tool_output_tail_lines,
                        )
                    blocks.append((_tool_result_block(name, content), False))
            case _:
                continue
    return blocks


def _tool_result_block(tool_name: str, content: str) -> RenderableType:
    header = Text.assemble(("✓ ", "green"), (tool_name, "bold"))
    # Resumed results carry only the stored content string (no typed result
    # model), so the body is the raw content with markup disabled, matching the
    # upstream resumed-result fidelity.
    header.append("\n")
    header.append(content, style="")
    return header
