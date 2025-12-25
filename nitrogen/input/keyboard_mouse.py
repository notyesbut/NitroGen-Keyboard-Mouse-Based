from __future__ import annotations

from typing import Any, Mapping, Set

from nitrogen.input.base import InputController

try:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _SENDINPUT_AVAILABLE = True
except Exception:
    _user32 = None
    _SENDINPUT_AVAILABLE = False

try:
    import pyautogui
except Exception:  # pragma: no cover - optional fallback
    pyautogui = None


DEFAULT_KEYS = (
    "w", "a", "s", "d",
    "space", "shift", "ctrl",
    "e", "q", "r",
    "tab", "esc",
    "up", "down", "left", "right",
    "c", "v",
)

DEFAULT_MOUSE_BUTTONS = ("left", "right", "middle")

VK_CODE = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "0": 0x30,
    "1": 0x31,
    "2": 0x32,
    "3": 0x33,
    "4": 0x34,
    "5": 0x35,
    "6": 0x36,
    "7": 0x37,
    "8": 0x38,
    "9": 0x39,
    "a": 0x41,
    "b": 0x42,
    "c": 0x43,
    "d": 0x44,
    "e": 0x45,
    "f": 0x46,
    "g": 0x47,
    "h": 0x48,
    "i": 0x49,
    "j": 0x4A,
    "k": 0x4B,
    "l": 0x4C,
    "m": 0x4D,
    "n": 0x4E,
    "o": 0x4F,
    "p": 0x50,
    "q": 0x51,
    "r": 0x52,
    "s": 0x53,
    "t": 0x54,
    "u": 0x55,
    "v": 0x56,
    "w": 0x57,
    "x": 0x58,
    "y": 0x59,
    "z": 0x5A,
}

EXTENDED_KEYS = {
    "up", "down", "left", "right",
    "insert", "delete", "home", "end", "pageup", "pagedown",
}

MOUSE_BUTTON_FLAGS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}


def _normalize_key(key: str) -> str:
    return key.strip().lower()


def _value_from_action(value: Any) -> int:
    if value is None:
        return 0
    if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
        try:
            return int(value[0])
        except Exception:
            pass
    try:
        return int(value)
    except Exception:
        return 0


if _SENDINPUT_AVAILABLE:
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", wintypes.ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", wintypes.ULONG_PTR),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
        ]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [
            ("type", wintypes.DWORD),
            ("u", _INPUT_UNION),
        ]

    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_EXTENDEDKEY = 0x0001
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_WHEEL = 0x0800


def _send_input(*inputs: "INPUT") -> None:
    if not _SENDINPUT_AVAILABLE:
        return
    n_inputs = len(inputs)
    if n_inputs == 0:
        return
    array = (INPUT * n_inputs)(*inputs)
    _user32.SendInput(n_inputs, array, ctypes.sizeof(INPUT))


class KeyboardMouseController(InputController):
    def __init__(self, dry_run: bool = False, backend: str = "sendinput") -> None:
        super().__init__(dry_run=dry_run)
        self.backend = backend
        self.pressed_keys: Set[str] = set()
        self.pressed_mouse_buttons: Set[str] = set()

        if self.backend == "sendinput" and not _SENDINPUT_AVAILABLE:
            self.backend = "pyautogui"

        if self.backend == "pyautogui" and pyautogui is None:
            raise ImportError("pyautogui is required for the keyboard/mouse fallback backend.")

    def step(self, action: Mapping[str, Any]) -> None:
        desired_keys = self._extract_keys(action.get("keys", []))
        desired_buttons = self._extract_buttons(action.get("mouse_buttons", []))
        dx = _value_from_action(action.get("mouse_dx", 0))
        dy = _value_from_action(action.get("mouse_dy", 0))
        wheel = _value_from_action(action.get("mouse_wheel", 0))

        keys_to_release = self.pressed_keys - desired_keys
        keys_to_press = desired_keys - self.pressed_keys
        buttons_to_release = self.pressed_mouse_buttons - desired_buttons
        buttons_to_press = desired_buttons - self.pressed_mouse_buttons

        if not self.dry_run:
            for key in keys_to_release:
                self._key_event(key, is_down=False)
            for key in keys_to_press:
                self._key_event(key, is_down=True)
            for button in buttons_to_release:
                self._mouse_button_event(button, is_down=False)
            for button in buttons_to_press:
                self._mouse_button_event(button, is_down=True)
            if dx != 0 or dy != 0:
                self._mouse_move(dx, dy)
            if wheel != 0:
                self._mouse_wheel(wheel)

        self.pressed_keys = desired_keys
        self.pressed_mouse_buttons = desired_buttons

    def reset(self) -> None:
        if not self.dry_run:
            for key in list(self.pressed_keys):
                self._key_event(key, is_down=False)
            for button in list(self.pressed_mouse_buttons):
                self._mouse_button_event(button, is_down=False)
        self.pressed_keys.clear()
        self.pressed_mouse_buttons.clear()

    def _extract_keys(self, raw: Any) -> Set[str]:
        if isinstance(raw, Mapping):
            keys = {k for k, v in raw.items() if v}
        elif isinstance(raw, (list, tuple, set)):
            keys = set(raw)
        else:
            keys = set()
        normalized = set()
        for key in keys:
            if not isinstance(key, str):
                continue
            name = _normalize_key(key)
            if name in VK_CODE:
                normalized.add(name)
        return normalized

    def _extract_buttons(self, raw: Any) -> Set[str]:
        if isinstance(raw, Mapping):
            buttons = {k for k, v in raw.items() if v}
        elif isinstance(raw, (list, tuple, set)):
            buttons = set(raw)
        else:
            buttons = set()
        normalized = set()
        for button in buttons:
            if not isinstance(button, str):
                continue
            name = button.strip().lower()
            if name in MOUSE_BUTTON_FLAGS:
                normalized.add(name)
        return normalized

    def _key_event(self, key: str, is_down: bool) -> None:
        if self.backend == "pyautogui":
            if is_down:
                pyautogui.keyDown(key)
            else:
                pyautogui.keyUp(key)
            return

        vk = VK_CODE.get(key)
        if vk is None:
            return
        flags = 0
        if not is_down:
            flags |= KEYEVENTF_KEYUP
        if key in EXTENDED_KEYS:
            flags |= KEYEVENTF_EXTENDEDKEY
        ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
        _send_input(INPUT(type=INPUT_KEYBOARD, ki=ki))

    def _mouse_move(self, dx: int, dy: int) -> None:
        if self.backend == "pyautogui":
            pyautogui.moveRel(dx, dy, duration=0)
            return
        mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0)
        _send_input(INPUT(type=INPUT_MOUSE, mi=mi))

    def _mouse_button_event(self, button: str, is_down: bool) -> None:
        if self.backend == "pyautogui":
            if is_down:
                pyautogui.mouseDown(button=button)
            else:
                pyautogui.mouseUp(button=button)
            return
        down_flag, up_flag = MOUSE_BUTTON_FLAGS[button]
        flag = down_flag if is_down else up_flag
        mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0)
        _send_input(INPUT(type=INPUT_MOUSE, mi=mi))

    def _mouse_wheel(self, amount: int) -> None:
        if self.backend == "pyautogui":
            pyautogui.scroll(amount)
            return
        mi = MOUSEINPUT(dx=0, dy=0, mouseData=amount, dwFlags=MOUSEEVENTF_WHEEL, time=0, dwExtraInfo=0)
        _send_input(INPUT(type=INPUT_MOUSE, mi=mi))
