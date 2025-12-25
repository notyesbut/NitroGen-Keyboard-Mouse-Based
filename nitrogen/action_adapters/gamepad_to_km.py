from __future__ import annotations

from typing import Any, Dict, Mapping

AXIS_SCALE = 32767.0
TRIGGER_SCALE = 255.0

DEFAULT_BUTTON_MAP = {
    "SOUTH": "space",
    "EAST": "e",
    "WEST": "q",
    "NORTH": "r",
    "LEFT_SHOULDER": "shift",
    "RIGHT_SHOULDER": "ctrl",
    "LEFT_THUMB": "c",
    "RIGHT_THUMB": "v",
    "BACK": "tab",
    "START": "esc",
    "DPAD_UP": "up",
    "DPAD_DOWN": "down",
    "DPAD_LEFT": "left",
    "DPAD_RIGHT": "right",
}

DEFAULT_TRIGGER_MAP = {
    "LEFT_TRIGGER": "right",
    "RIGHT_TRIGGER": "left",
}


def _value_from_action(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
        try:
            return float(value[0])
        except Exception:
            pass
    try:
        return float(value)
    except Exception:
        return 0.0


def _axis_norm(value: Any) -> float:
    raw = _value_from_action(value)
    if raw > AXIS_SCALE:
        raw = AXIS_SCALE
    if raw < -AXIS_SCALE:
        raw = -AXIS_SCALE
    return raw / AXIS_SCALE


def _trigger_norm(value: Any) -> float:
    raw = _value_from_action(value)
    if raw > TRIGGER_SCALE:
        raw = TRIGGER_SCALE
    if raw < 0:
        raw = 0
    return raw / TRIGGER_SCALE


def gamepad_action_to_km(
    action: Mapping[str, Any],
    button_map: Mapping[str, str] | None = None,
    trigger_map: Mapping[str, str] | None = None,
    mouse_sens: float = 15.0,
    axis_deadzone: float = 0.2,
    mouse_max: int | None = None,
    trigger_threshold: float = 0.1,
) -> Dict[str, Any]:
    button_map = button_map or DEFAULT_BUTTON_MAP
    trigger_map = trigger_map or DEFAULT_TRIGGER_MAP

    keys = set()
    mouse_buttons = set()

    for btn, key in button_map.items():
        if _value_from_action(action.get(btn, 0)) > 0:
            keys.add(key)

    for trig, button in trigger_map.items():
        if _trigger_norm(action.get(trig, 0)) >= trigger_threshold:
            mouse_buttons.add(button)

    lx = _axis_norm(action.get("AXIS_LEFTX", 0))
    ly = _axis_norm(action.get("AXIS_LEFTY", 0))
    if lx > axis_deadzone:
        keys.add("d")
    elif lx < -axis_deadzone:
        keys.add("a")
    if ly > axis_deadzone:
        keys.add("w")
    elif ly < -axis_deadzone:
        keys.add("s")

    rx = _axis_norm(action.get("AXIS_RIGHTX", 0))
    ry = _axis_norm(action.get("AXIS_RIGHTY", 0))
    mouse_dx = int(round(rx * mouse_sens))
    mouse_dy = int(round(-ry * mouse_sens))

    if mouse_max is not None:
        if mouse_dx > mouse_max:
            mouse_dx = mouse_max
        if mouse_dx < -mouse_max:
            mouse_dx = -mouse_max
        if mouse_dy > mouse_max:
            mouse_dy = mouse_max
        if mouse_dy < -mouse_max:
            mouse_dy = -mouse_max

    return {
        "keys": sorted(keys),
        "mouse_dx": mouse_dx,
        "mouse_dy": mouse_dy,
        "mouse_buttons": sorted(mouse_buttons),
        "mouse_wheel": 0,
    }
