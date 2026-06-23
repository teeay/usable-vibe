"""Adapters from upstream Textual widgets to native-scroll Rich blocks.

This module is the quarantine boundary for widget presentation coupling. It may
depend on upstream widget classes and selected private presentation fields, but
that access stays here instead of spreading through ``VibeApp`` or the
committer. It also renders semantic hook-run groups (``render_hook_run``), the
native equivalent of the full-screen ``HookRunContainer``, reusing the same
shared severity presentation rather than scraping the container.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.style import Style
from rich.text import Text
from textual.widget import Widget

from vibe.cli.textual_ui.native_scroll.presentation import (
    HOOK_SEVERITY_ICONS,
    HOOK_SEVERITY_STYLES,
)
from vibe.cli.textual_ui.widgets.messages import (
    ErrorMessage,
    HookSystemMessageLine,
    InterruptMessage,
    UserCommandMessage,
    UserMessage,
    WarningMessage,
)
from vibe.core.hooks.models import HookMessageSeverity, HookType
from vibe.core.types import FileImageSource, ImageAttachment, InlineImageSource

# User-prompt band: a warm gray-red background that sets prompts apart from the
# response transcript without a separator rule. These are deliberate RGB values,
# not ANSI-palette colors — the 16-color ANSI set has no desaturated red-gray, so
# this is the one fixed color in the prompt block even under ANSI themes. The
# light/dark variants are chosen by the active theme's darkness.
_PROMPT_BAND_DARK = Style(color="bright_white", bgcolor="#4a3c3c")
_PROMPT_BAND_LIGHT = Style(color="black", bgcolor="#e3d4d4")


def render_widget_block(  # noqa: PLR0911
    widget: Widget, *, dark: bool = True
) -> RenderableType | None:
    if isinstance(widget, UserMessage):
        if widget.pending:
            return None  # Queued prompts stay live until drained.
        return render_user_prompt(
            widget.PROMPT_CHAR, widget.get_content(), widget._images, dark=dark
        )
    if isinstance(widget, UserCommandMessage):
        return Markdown(widget._content)
    # WhatsNewMessage is a startup notice, not conversation transcript: it is
    # omitted from scrollback by design, so it is not consumed here.
    if isinstance(widget, ErrorMessage):
        return Text(f"Error: {widget._error}", style="red")
    if isinstance(widget, WarningMessage):
        return Text(widget._message, style="yellow")
    if isinstance(widget, InterruptMessage):
        return Text("Interrupted · What should Vibe do instead?", style="yellow")
    if isinstance(widget, HookSystemMessageLine):
        return render_hook_line(widget._hook_name, widget._content, widget._severity)
    return None


def render_hook_line(
    hook_name: str, content: str, severity: HookMessageSeverity
) -> Text:
    icon = HOOK_SEVERITY_ICONS.get(severity, "⚠")
    style = HOOK_SEVERITY_STYLES.get(severity, "yellow")
    return Text.assemble((f"{icon} ", style), (f"[{hook_name}] {content}", ""))


def render_hook_run(
    *,
    scope: HookType,
    tool_name: str | None,
    lines: Sequence[tuple[str, str, HookMessageSeverity]],
) -> RenderableType:
    """Render one hook run as a grouped Rich block.

    Native scrollback cannot mount before/after-tool hook output spatially above
    or below the tool widget the way the full-screen ``HookRunContainer`` does,
    so a dim association header carries the scope/tool relationship instead.
    """
    header = Text(_hook_run_header(scope, tool_name), style="dim")
    rows: list[RenderableType] = [header]
    rows.extend(
        render_hook_line(hook_name, content, severity)
        for hook_name, content, severity in lines
    )
    return Group(*rows)


def _hook_run_header(scope: HookType, tool_name: str | None) -> str:
    tool = tool_name or "tool"
    match scope:
        case HookType.BEFORE_TOOL:
            return f"before {tool}"
        case HookType.AFTER_TOOL:
            return f"after {tool}"
        case _:
            return "post-agent-turn"


def render_user_prompt(
    prompt_char: str,
    content: str,
    images: list[ImageAttachment] | None = None,
    *,
    dark: bool = True,
) -> RenderableType:
    # The prompt marker stays bold; color comes from the band so the text reads
    # white on the dark band and black on the light band, per the band style.
    prompt = Text.assemble((f"{prompt_char} ", "bold"), (content, ""))
    rows: list[RenderableType] = [prompt]
    if images:
        rows.append(_attachments_line(images))
    body: RenderableType = Group(*rows) if len(rows) > 1 else prompt
    # A full-width warm gray-red band sets the prompt apart from the response
    # transcript; it replaces the previous ExpandingSeparator rule. ``expand``
    # fills the band to the terminal width so the background spans the whole row.
    band = _PROMPT_BAND_DARK if dark else _PROMPT_BAND_LIGHT
    return Padding(body, (0, 1), style=band, expand=True)


def _attachments_line(images: list[ImageAttachment]) -> RenderableType:
    label = "attached image" if len(images) == 1 else "attached images"
    line = Text(f"└ {label}: ", style="dim")
    for index, image in enumerate(images):
        if index:
            line.append(", ", style="dim")
        # Carry a file:// terminal hyperlink on the alias, matching the widget's
        # clickable attachment links. Inline images have no durable file target.
        match image.source:
            case FileImageSource(path=path):
                line.append(image.alias, style=Style(link=path.as_uri(), dim=True))
            case InlineImageSource():
                line.append(image.alias, style="dim")
    return line
