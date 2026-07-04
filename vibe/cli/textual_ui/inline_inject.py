"""Compatibility imports for native-scroll terminal injection helpers."""

from __future__ import annotations

from vibe.cli.textual_ui.native_scroll.inline_inject import (
    build_bottom_anchor,
    build_bottom_reset,
    build_commit_injection,
    build_inline_terminal_reset,
    build_inline_terminal_setup,
    build_relocated_anchor,
    build_resize_sweep,
    build_scroll_region_commit_injection,
)

__all__ = [
    "build_bottom_anchor",
    "build_bottom_reset",
    "build_commit_injection",
    "build_inline_terminal_reset",
    "build_inline_terminal_setup",
    "build_relocated_anchor",
    "build_resize_sweep",
    "build_scroll_region_commit_injection",
]
