"""Single-writer scrollback committer for native terminal-scroll mode.

This is the terminal-mode sibling of :class:`EventHandler`. Where ``EventHandler``
renders durable transcript content into Textual widgets mounted under
``#messages``, the committer renders the same semantic content into Rich blocks
and queues them as terminal lines. Those lines are injected into the host
terminal's native scrollback by ``VibeApp._display`` (the single coordinated
writer) on Textual's message-loop thread, so commits never interleave with the
live-region repaint.

The committer is created once and owned by ``VibeApp``. It holds streaming
buffer state (assistant / reasoning chunks) across events and is closed on
shutdown. It never opens a second stream or ``os.dup``s the terminal: it only
builds renderables, renders them to lines on demand, and asks the app to
refresh so ``_display`` drains the queue.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from io import StringIO
import re

from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.widget import Widget

from vibe.cli.textual_ui.native_scroll.app_surfaces import (
    render_approval_outcome,
    render_plan_notice,
    render_rewind_outcome,
    render_startup_header,
    render_teleport_outcome,
)
from vibe.cli.textual_ui.native_scroll.history_render import render_history_blocks
from vibe.cli.textual_ui.native_scroll.tool_result_render import (
    render_manual_bash_body,
    render_result_body,
)
from vibe.cli.textual_ui.native_scroll.widget_render import (
    render_hook_line,
    render_hook_run,
    render_widget_block,
)
from vibe.core.hooks.models import (
    HookEndEvent,
    HookEvent,
    HookMessageSeverity,
    HookRunEndEvent,
    HookRunStartEvent,
    HookType,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIDataAdapter
from vibe.core.types import (
    AgentProfileChangedEvent,
    AssistantEvent,
    BaseEvent,
    CompactEndEvent,
    CompactStartEvent,
    LLMMessage,
    PlanReviewEndedEvent,
    PlanReviewRequestedEvent,
    ReasoningEvent,
    SessionTitleUpdatedEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserMessageEvent,
    WaitingForInputEvent,
)

# A Markdown list item, allowing up to three leading spaces (CommonMark) and
# either a bullet (-, *, +) or an ordered marker (digits followed by . or )).
_LIST_ITEM_RE = re.compile(r"^ {0,3}([-*+]|\d{1,9}[.)])\s")
# A fenced code block delimiter (``` or ~~~), again allowing slight indentation.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class _Block:
    """A scanned top-level Markdown block within a streaming buffer.

    ``next_offset`` is the character offset in the buffer where the following
    block begins (after the blank-line separator). ``terminated`` is True when a
    blank-line separator closed the block, so it is safe to commit. ``open_fence``
    marks a block whose code fence is still open (the stream has not closed it
    yet). ``is_list`` marks a block that begins with a list item so consecutive
    list blocks can be grouped and never split mid-list.
    """

    is_list: bool
    terminated: bool
    open_fence: bool
    next_offset: int


@dataclass
class _HookRun:
    """A pending hook run group, the native sibling of ``HookRunContainer``.

    ``lines`` collects each non-empty ``HookEndEvent`` in the run as
    ``(hook_name, content, severity)``. The group is committed as one Rich block
    when the matching ``HookRunEndEvent`` arrives, and an empty run commits
    nothing (mirroring the container's ``display=False`` behavior).
    """

    scope: HookType
    tool_name: str | None
    lines: list[tuple[str, str, HookMessageSeverity]]


class ScrollbackCommitter:
    """Renders durable transcript content to native-scrollback lines.

    Args:
        width_getter: Returns the current usable terminal width. Called every
            drain so wrapping tracks terminal resizes.
        refresh: Called after a block is queued so the owner (``VibeApp``) can
            schedule a frame; ``_display`` then drains the queue. Its return
            value is ignored. Defaults to a no-op so the committer can be
            unit-tested without Textual.
        color_system: Rich color system used when rendering blocks to ANSI.
        dark: Returns whether the active theme is dark; used to pick the syntax
            theme for durable tool-result bodies (e.g. edit diffs). Defaults to
            dark so the committer can be unit-tested without an app.
        ansi: Returns whether the terminal is restricted to ANSI colors; used to
            pick the ANSI syntax theme and dim removed diff lines. Defaults to
            ``False``.
    """

    def __init__(
        self,
        *,
        width_getter: Callable[[], int],
        refresh: Callable[[], object] | None = None,
        color_system: str | None = "truecolor",
        dark: Callable[[], bool] | None = None,
        ansi: Callable[[], bool] | None = None,
    ) -> None:
        self._width_getter = width_getter
        self._refresh: Callable[[], object] = refresh or (lambda: None)
        self._color_system = color_system
        self._dark: Callable[[], bool] = dark or (lambda: True)
        self._ansi: Callable[[], bool] = ansi or (lambda: False)
        self._queue: list[RenderableType] = []
        self._assistant_buffer = ""
        self._reasoning_buffer = ""
        # The reasoning header ("✻ Thinking") is emitted once per reasoning run;
        # subsequent committed paragraphs of the same run omit it so a streamed
        # multi-paragraph reasoning block is not repeatedly re-headed.
        self._reasoning_open = False
        self._tool_calls: dict[str, ToolCallDisplay] = {}
        # Pending hook run groups, keyed by scope + tool_call_id (mirroring
        # EventHandler._hook_container_key) so interleaved tool hook chains do
        # not merge across calls.
        self._hook_runs: dict[str, _HookRun] = {}
        self._closed = False

    # -- queue / drain -----------------------------------------------------

    @property
    def has_pending(self) -> bool:
        return bool(self._queue)

    def _enqueue(self, block: RenderableType) -> None:
        if self._closed:
            return
        self._queue.append(block)
        self._refresh()

    def drain_lines(self) -> list[str]:
        """Render and return queued blocks as terminal lines, clearing the queue.

        Each line is ANSI-styled text without a trailing newline; the caller
        terminates lines with ``\\r\\n``. A blank line separates blocks.
        """
        if not self._queue:
            return []
        blocks = self._queue
        self._queue = []
        width = max(1, self._width_getter())
        lines: list[str] = []
        for block in blocks:
            lines.extend(self._render_block(block, width))
            lines.append("")
        return lines

    def _render_block(self, block: RenderableType, width: int) -> list[str]:
        console = Console(
            width=width,
            file=StringIO(),
            force_terminal=True,
            color_system=self._color_system,  # type: ignore[arg-type]
            highlight=False,
            soft_wrap=False,
        )
        with console.capture() as capture:
            console.print(block)
        rendered = capture.get().split("\n")
        while rendered and rendered[-1] == "":
            rendered.pop()
        return rendered

    # -- streaming buffers -------------------------------------------------
    #
    # Streaming content is committed to native scrollback one completed
    # paragraph (top-level Markdown block) at a time, so a long answer flows up
    # into scrollback as it is produced instead of being held until a flush
    # boundary and then dumped as a single terminal-height render. The trailing,
    # still-incomplete block stays buffered (live) until the next chunk
    # completes it or a flush boundary (tool call, turn end) forces it out. Each
    # committed prefix is a verbatim prefix of the original stream, so the
    # rendered Markdown is identical to rendering the whole message at once.

    def _commit_ready_assistant(self) -> None:
        commit, self._assistant_buffer = self._split_committable_blocks(
            self._assistant_buffer
        )
        if commit.strip():
            self._enqueue(Markdown(commit))

    def _flush_assistant(self) -> None:
        buffer = self._assistant_buffer
        self._assistant_buffer = ""
        if buffer.strip():
            self._enqueue(Markdown(buffer))

    def _commit_ready_reasoning(self) -> None:
        commit, self._reasoning_buffer = self._split_committable_blocks(
            self._reasoning_buffer
        )
        if commit.strip():
            self._emit_reasoning(commit)

    def _flush_reasoning(self) -> None:
        buffer = self._reasoning_buffer
        self._reasoning_buffer = ""
        if buffer.strip():
            self._emit_reasoning(buffer)
        # The reasoning run is closed at a flush boundary; the next run starts a
        # fresh header.
        self._reasoning_open = False

    def _emit_reasoning(self, text: str) -> None:
        # Reasoning policy: render as dimmed Markdown so lists, code, and emphasis
        # survive, under a single "✻ Thinking" header per run. No internal
        # scroll/collapse -- long reasoning flows up as completed paragraphs via
        # the same paragraph-streaming split as assistant text.
        body = Markdown(text.strip(), style="dim")
        if self._reasoning_open:
            self._enqueue(body)
            return
        self._reasoning_open = True
        self._enqueue(Group(Text("✻ Thinking", style="dim italic"), body))

    def _split_committable_blocks(self, buffer: str) -> tuple[str, str]:
        """Split ``buffer`` into a committable prefix and a live remainder.

        The prefix contains only complete top-level Markdown blocks that render
        identically when committed on their own; the remainder holds the trailing
        block that may still be growing. Fenced code blocks are never split, and
        consecutive list blocks are grouped so list numbering is preserved.
        """
        blocks = self._scan_blocks(buffer)
        if len(blocks) <= 1:
            # Only the trailing (possibly incomplete) block exists; keep it live.
            return "", buffer
        boundary = 0
        for block in blocks[:-1]:
            if block.open_fence or not block.terminated:
                break
            boundary = block.next_offset
        if boundary <= 0:
            return "", buffer
        return buffer[:boundary], buffer[boundary:]

    def _scan_blocks(self, buffer: str) -> list[_Block]:
        lines = buffer.split("\n")
        starts: list[int] = []
        pos = 0
        for line in lines:
            starts.append(pos)
            pos += len(line) + 1  # account for the "\n" separating lines
        end = len(buffer)

        blocks: list[_Block] = []
        i = 0
        n = len(lines)
        while i < n:
            if lines[i].strip() == "":
                i += 1  # skip blank separator lines between blocks
                continue
            is_list = bool(_LIST_ITEM_RE.match(lines[i]))
            in_fence = False
            while i < n:
                line = lines[i]
                if _FENCE_RE.match(line):
                    in_fence = not in_fence
                    i += 1
                    continue
                if not in_fence and line.strip() == "":
                    break  # blank line closes the block (outside a fence)
                i += 1
            open_fence = in_fence
            sep_start = i
            while i < n and lines[i].strip() == "":
                i += 1
            terminated = i > sep_start and not open_fence
            next_offset = starts[i] if i < n else end
            blocks.append(_Block(is_list, terminated, open_fence, next_offset))
        return self._merge_list_blocks(blocks)

    def _merge_list_blocks(self, blocks: list[_Block]) -> list[_Block]:
        merged: list[_Block] = []
        for block in blocks:
            if merged and block.is_list and merged[-1].is_list:
                # Loose lists put blank lines between items; keep the whole list
                # as one block so committing it never restarts the numbering.
                merged[-1] = _Block(
                    is_list=True,
                    terminated=block.terminated,
                    open_fence=block.open_fence,
                    next_offset=block.next_offset,
                )
            else:
                merged.append(block)
        return merged

    def flush(self) -> None:
        """Flush any buffered streaming content to the queue."""
        self._flush_reasoning()
        self._flush_assistant()

    def close(self) -> None:
        self.flush()
        self._closed = True

    # -- event entry point -------------------------------------------------

    def handle_event(self, event: BaseEvent) -> None:
        """Route an ``AgentLoop`` event to durable scrollback or buffers."""
        match event:
            case AssistantEvent():
                self._flush_reasoning()
                self._assistant_buffer += event.content
                self._commit_ready_assistant()
            case ReasoningEvent():
                self._flush_assistant()
                self._reasoning_buffer += event.content
                self._commit_ready_reasoning()
            case ToolCallEvent():
                self.flush()
                self._record_tool_call(event)
            case ToolResultEvent():
                self.flush()
                self._commit_tool_result(event)
            case ToolStreamEvent():
                pass  # Live-only: active tool progress stays in the live region.
            case CompactStartEvent():
                self.flush()  # Live-only status while compacting.
            case CompactEndEvent():
                self.flush()
                self._commit_compact_end(event)
            case UserMessageEvent():
                # Local prompts are already committed via render_widget (the app
                # mounts a UserMessage before the turn); committing here too would
                # duplicate them.
                self.flush()
            case HookEvent():
                self._handle_hook_event(event)
            case WaitingForInputEvent():
                self.flush()
            case PlanReviewRequestedEvent():
                # The live PlanFileMessage (and Ctrl+G state) is owned by the app;
                # the committer records the durable "plan ready" notice.
                self.flush()
                self._enqueue(render_plan_notice(event.file_path))
            case (
                AgentProfileChangedEvent()
                | SessionTitleUpdatedEvent()
                | PlanReviewEndedEvent()
            ):
                pass  # No durable transcript line; handled live by the app.
            case _:
                self.flush()
                self._enqueue(Text(str(event), style="dim"))

    # -- tool rendering ----------------------------------------------------

    def _record_tool_call(self, event: ToolCallEvent) -> None:
        adapter = ToolUIDataAdapter(event.tool_class)
        self._tool_calls[event.tool_call_id] = adapter.get_call_display(event)

    def _commit_tool_result(self, event: ToolResultEvent) -> None:
        call = self._tool_calls.pop(event.tool_call_id, None)
        if event.tool_class is not None:
            result = ToolUIDataAdapter(event.tool_class).get_result_display(event)
        else:
            result = ToolResultDisplay(
                success=event.error is None and not event.skipped,
                message=event.error or event.skip_reason or "Done",
            )
        body = render_result_body(
            event.tool_name, event.result, dark=self._dark(), ansi=self._ansi()
        )
        self._enqueue(self._tool_block(event.tool_name, call, result, body))

    def _tool_block(
        self,
        tool_name: str,
        call: ToolCallDisplay | None,
        result: ToolResultDisplay,
        body: RenderableType | None = None,
    ) -> RenderableType:
        icon = "✓" if result.success else "✗"
        icon_style = "green" if result.success else "red"
        summary = call.summary if call is not None else tool_name
        header = Text.assemble((f"{icon} ", icon_style), (summary, "bold"))
        if result.suffix:
            header.append(f" {result.suffix}", style="dim")
        renderables: list[RenderableType] = [header]
        # The durable body carries the full detail (output, diff, answers), so
        # the generic summary line is dropped to avoid duplicating it.
        message = result.message.strip()
        if body is None and message and message.lower() not in {"success", "done"}:
            renderables.append(Text(message, style="" if result.success else "red"))
        for warning in result.warnings:
            renderables.append(Text(f"⚠ {warning}", style="yellow"))
        if body is not None:
            renderables.append(body)
        return Group(*renderables) if len(renderables) > 1 else header

    def commit_manual_bash(
        self, command: str, output: str, exit_code: int, *, interrupted: bool = False
    ) -> None:
        """Commit a durable block for a finished manual ``!`` / queued bash.

        Used by both manual and queued bash so the live-to-durable finalization
        has one path. The live ``BashOutputMessage`` streams in ``#live-queue``
        while running; this commits the final result and the app removes the
        live widget.
        """
        self.flush()
        self._enqueue(
            render_manual_bash_body(command, output, exit_code, interrupted=interrupted)
        )

    def commit_history(
        self,
        messages: Sequence[LLMMessage],
        tool_call_map: dict[str, str],
        *,
        omitted_count: int,
    ) -> None:
        """Commit resumed/rehydrated session history to native scrollback.

        Replaces the hidden ``_mount_history_batch`` path in native mode: the
        recent tail is rendered semantically from the ``LLMMessage`` records and
        a leading marker records any earlier messages dropped before the tail.
        """
        self.flush()
        for block in render_history_blocks(
            messages, tool_call_map, omitted_count=omitted_count
        ):
            self._enqueue(block)

    def commit_startup_header(self, *, version: str, model: str, cwd: str) -> None:
        """Commit the compact durable session header once at startup."""
        self.flush()
        self._enqueue(render_startup_header(version=version, model=model, cwd=cwd))

    def commit_teleport(self, *, url: str | None, error: str | None) -> None:
        """Commit the durable teleport outcome line.

        The live ``TeleportMessage`` spinner is owned by the app while teleporting;
        this commits the final result and the app removes the live widget.
        """
        self.flush()
        self._enqueue(render_teleport_outcome(url=url, error=error))

    def commit_approval(
        self, *, tool_name: str, approved: bool, scope: str | None = None
    ) -> None:
        """Commit the durable approval allow/deny outcome.

        The ``ApprovalApp`` form stays live; this records the safety-relevant
        decision once when the approval future resolves. ``scope`` annotates a
        persisted allow (e.g. "always for this tool").
        """
        self.flush()
        self._enqueue(
            render_approval_outcome(tool_name=tool_name, approved=approved, scope=scope)
        )

    def commit_rewind(
        self, preview: str, *, restored_files: bool, discarded: int
    ) -> None:
        """Commit the durable rewind fork marker.

        Native scrollback cannot erase committed transcript, so the rewind records
        a marker at the fork point rather than removing prior output.
        """
        self.flush()
        self._enqueue(
            render_rewind_outcome(
                preview, restored_files=restored_files, discarded=discarded
            )
        )

    def _commit_compact_end(self, event: CompactEndEvent) -> None:
        self._enqueue(Text("✓ Conversation compacted", style="green"))

    def _handle_hook_event(self, event: HookEvent) -> None:
        match event:
            case HookRunStartEvent():
                self.flush()
                self._begin_hook_run(event)
            case HookRunEndEvent():
                self._commit_hook_run(event)
            case HookEndEvent():
                self._record_hook_end(event)
            case _:
                pass  # HookStartEvent is live-only loading status.

    @staticmethod
    def _hook_run_key(scope: HookType, tool_call_id: str | None) -> str:
        # Mirror EventHandler._hook_container_key so native grouping matches the
        # full-screen container keying exactly.
        if scope == HookType.POST_AGENT_TURN:
            return "agent_turn"
        return f"{scope.value}:{tool_call_id or ''}"

    def _begin_hook_run(self, event: HookRunStartEvent) -> None:
        key = self._hook_run_key(event.scope, event.tool_call_id)
        self._hook_runs[key] = _HookRun(
            scope=event.scope, tool_name=event.tool_name, lines=[]
        )

    def _record_hook_end(self, event: HookEndEvent) -> None:
        if not event.content:
            return
        key = self._hook_run_key(event.scope, event.tool_call_id)
        run = self._hook_runs.get(key)
        if run is not None:
            run.lines.append((event.hook_name, event.content, event.status))
            return
        # Stray HookEndEvent without an open run: commit the single line
        # directly, preserving backward-compatible behavior.
        self.flush()
        self._enqueue(render_hook_line(event.hook_name, event.content, event.status))

    def _commit_hook_run(self, event: HookRunEndEvent) -> None:
        key = self._hook_run_key(event.scope, event.tool_call_id)
        run = self._hook_runs.pop(key, None)
        if run is None or not run.lines:
            return  # Empty runs commit nothing (container display=False).
        self._enqueue(
            render_hook_run(scope=run.scope, tool_name=run.tool_name, lines=run.lines)
        )

    # -- widget transition shim -------------------------------------------

    def render_widget(self, widget: Widget) -> bool:
        """Commit a durable app-generated widget; return ``True`` if consumed.

        Live or interactive widgets (tool calls, spinners, queue headers, bottom
        apps) are not consumed and still mount into the (hidden) chat area.
        """
        if self._closed:
            return False
        block = render_widget_block(widget)
        if block is None:
            return False
        self.flush()
        self._enqueue(block)
        return True
