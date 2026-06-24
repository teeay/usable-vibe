"""Workarounds for VS Code terminal quirks affecting Textual widgets."""

from __future__ import annotations

from textual import events
from textual.reactive import Reactive, reactive
from textual.widgets import Input


def patch_vscode_space(event: events.Key) -> None:
    """Patch space key events sent as CSI u by VS Code 1.110+.

    VS Code encodes space as ``\\x1b[32u`` (CSI u), which Textual parses as
    ``Key("space", character=None, is_printable=False)``.  Input widgets then
    silently drop the keystroke because there is no printable character.
    Assigning ``event.character = " "`` restores normal behaviour.
    """
    if event.key in {"space", "shift+space"} and event.character is None:
        event.character = " "


class VscodeCompatInput(Input):
    """``Input`` subclass that handles the VS Code CSI-u space quirk."""

    # Native-scroll patch point: mirror ``ChatTextArea`` and never use Textual's
    # blinking caret. The base ``Input`` blink timer
    # (``_toggle_cursor_blink_visible``, fired every 0.5s while focused)
    # refreshes the input, and in inline mode each refresh emits a full
    # ``InlineUpdate`` frame that repaints the whole live region twice a second
    # at idle. These inputs back the live question "Other" field and proxy setup
    # dialog, so blinking would redraw those forms while they wait on the user.
    # The ``False`` default covers the initial on-mount focus so the timer never
    # starts; ``validate_cursor_blink`` coerces every later assignment off.
    cursor_blink: Reactive[bool] = reactive(False, init=False)

    def validate_cursor_blink(self, value: bool) -> bool:
        return False

    async def _on_key(self, event: events.Key) -> None:
        patch_vscode_space(event)
        await super()._on_key(event)
