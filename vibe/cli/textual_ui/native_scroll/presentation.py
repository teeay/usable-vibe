"""Shared presentation constants for native scrollback renderers."""

from __future__ import annotations

from vibe.core.hooks.models import HookMessageSeverity

HOOK_SEVERITY_ICONS: dict[HookMessageSeverity, str] = {
    HookMessageSeverity.OK: "✓",
    HookMessageSeverity.WARNING: "⚠",
    HookMessageSeverity.ERROR: "✗",
}
HOOK_SEVERITY_STYLES: dict[HookMessageSeverity, str] = {
    HookMessageSeverity.OK: "green",
    HookMessageSeverity.WARNING: "yellow",
    HookMessageSeverity.ERROR: "red",
}
