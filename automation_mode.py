from __future__ import annotations


OFF = "off"
HALF_AUTO = "half_auto"
FULL_AUTO = "full_auto"
AUTOMATION_MODES = (OFF, HALF_AUTO, FULL_AUTO)


def automation_mode(auto_recognize: bool, managed_auto_reply: bool) -> str:
    """Map the two legacy switches to the single user-facing mode."""
    if bool(managed_auto_reply):
        return FULL_AUTO
    if bool(auto_recognize):
        return HALF_AUTO
    return OFF


def automation_flags(mode: str) -> tuple[bool, bool]:
    """Return ``(auto_recognize, managed_auto_reply)`` for a mode."""
    if mode == HALF_AUTO:
        return True, False
    if mode == FULL_AUTO:
        return True, True
    if mode == OFF:
        return False, False
    raise ValueError(f"unknown automation mode: {mode}")


def next_automation_mode(current: str, selected: str) -> str:
    """Return the explicitly selected menu mode."""
    if selected not in AUTOMATION_MODES:
        raise ValueError(f"unknown selectable automation mode: {selected}")
    return selected


def automation_mode_label(mode: str) -> str:
    return "模式选择  ▾"
