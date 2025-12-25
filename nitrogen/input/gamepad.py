from __future__ import annotations

import platform
from typing import Any, Mapping

from nitrogen.input.base import InputController

try:
    import vgamepad as vg
except Exception as exc:  # pragma: no cover - only used if vgamepad missing
    vg = None
    _VGAMEPAD_IMPORT_ERROR = exc


XBOX_MAPPING = {
    "DPAD_UP": "XUSB_GAMEPAD_DPAD_UP",
    "DPAD_DOWN": "XUSB_GAMEPAD_DPAD_DOWN",
    "DPAD_LEFT": "XUSB_GAMEPAD_DPAD_LEFT",
    "DPAD_RIGHT": "XUSB_GAMEPAD_DPAD_RIGHT",
    "START": "XUSB_GAMEPAD_START",
    "BACK": "XUSB_GAMEPAD_BACK",
    "LEFT_SHOULDER": "XUSB_GAMEPAD_LEFT_SHOULDER",
    "RIGHT_SHOULDER": "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "GUIDE": "XUSB_GAMEPAD_GUIDE",
    "WEST": "XUSB_GAMEPAD_X",
    "SOUTH": "XUSB_GAMEPAD_A",
    "EAST": "XUSB_GAMEPAD_B",
    "NORTH": "XUSB_GAMEPAD_Y",
    "LEFT_TRIGGER": "LEFT_TRIGGER",
    "RIGHT_TRIGGER": "RIGHT_TRIGGER",
    "AXIS_LEFTX": "LEFT_JOYSTICK",
    "AXIS_LEFTY": "LEFT_JOYSTICK",
    "AXIS_RIGHTX": "RIGHT_JOYSTICK",
    "AXIS_RIGHTY": "RIGHT_JOYSTICK",
    "LEFT_THUMB": "XUSB_GAMEPAD_LEFT_THUMB",
    "RIGHT_THUMB": "XUSB_GAMEPAD_RIGHT_THUMB",
}

PS4_MAPPING = {
    "DPAD_UP": "DS4_BUTTON_DPAD_NORTH",
    "DPAD_DOWN": "DS4_BUTTON_DPAD_SOUTH",
    "DPAD_LEFT": "DS4_BUTTON_DPAD_WEST",
    "DPAD_RIGHT": "DS4_BUTTON_DPAD_EAST",
    "START": "DS4_BUTTON_OPTIONS",
    "BACK": "DS4_BUTTON_SHARE",
    "LEFT_SHOULDER": "DS4_BUTTON_SHOULDER_LEFT",
    "RIGHT_SHOULDER": "DS4_BUTTON_SHOULDER_RIGHT",
    "GUIDE": "DS4_BUTTON_GUIDE",
    "WEST": "DS4_BUTTON_SQUARE",
    "SOUTH": "DS4_BUTTON_CROSS",
    "EAST": "DS4_BUTTON_CIRCLE",
    "NORTH": "DS4_BUTTON_TRIANGLE",
    "LEFT_TRIGGER": "LEFT_TRIGGER",
    "RIGHT_TRIGGER": "RIGHT_TRIGGER",
    "AXIS_LEFTX": "LEFT_JOYSTICK",
    "AXIS_LEFTY": "LEFT_JOYSTICK",
    "AXIS_RIGHTX": "RIGHT_JOYSTICK",
    "AXIS_RIGHTY": "RIGHT_JOYSTICK",
    "LEFT_THUMB": "DS4_BUTTON_THUMB_LEFT",
    "RIGHT_THUMB": "DS4_BUTTON_THUMB_RIGHT",
}


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


class GamepadController(InputController):
    def __init__(self, controller_type: str = "xbox", system: str | None = None, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)

        if vg is None:
            raise ImportError(
                "vgamepad is required for gamepad control but is not installed."
            ) from _VGAMEPAD_IMPORT_ERROR

        self.controller_type = controller_type
        self.system = system or platform.system().lower()

        if controller_type == "xbox":
            self.gamepad = vg.VX360Gamepad()
            self.mapping = XBOX_MAPPING
        elif controller_type == "ps4":
            self.gamepad = vg.VDS4Gamepad()
            self.mapping = PS4_MAPPING
        else:
            raise ValueError("Unsupported controller type")

        self.left_joystick_x = 0
        self.left_joystick_y = 0
        self.right_joystick_x = 0
        self.right_joystick_y = 0

    def step(self, action: Mapping[str, Any]) -> None:
        if self.dry_run:
            return

        self.gamepad.reset()

        for control in [
            "EAST",
            "SOUTH",
            "NORTH",
            "WEST",
            "BACK",
            "GUIDE",
            "START",
            "DPAD_DOWN",
            "DPAD_LEFT",
            "DPAD_RIGHT",
            "DPAD_UP",
            "LEFT_SHOULDER",
            "RIGHT_SHOULDER",
            "LEFT_THUMB",
            "RIGHT_THUMB",
        ]:
            if control in action:
                if bool(action[control]):
                    self.press_button(control)
                else:
                    self.release_button(control)

        if "LEFT_TRIGGER" in action:
            self.set_trigger("LEFT_TRIGGER", _value_from_action(action["LEFT_TRIGGER"]))
        if "RIGHT_TRIGGER" in action:
            self.set_trigger("RIGHT_TRIGGER", _value_from_action(action["RIGHT_TRIGGER"]))

        if "AXIS_LEFTX" in action and "AXIS_LEFTY" in action:
            self.set_joystick("AXIS_LEFTX", _value_from_action(action["AXIS_LEFTX"]))
            self.set_joystick("AXIS_LEFTY", _value_from_action(action["AXIS_LEFTY"]))

        if "AXIS_RIGHTX" in action and "AXIS_RIGHTY" in action:
            self.set_joystick("AXIS_RIGHTX", _value_from_action(action["AXIS_RIGHTX"]))
            self.set_joystick("AXIS_RIGHTY", _value_from_action(action["AXIS_RIGHTY"]))

        self.gamepad.update()

    def press_button(self, button: str) -> None:
        if self.dry_run:
            return
        button_mapped = self.mapping.get(button)
        if self.controller_type == "xbox":
            self.gamepad.press_button(button=getattr(vg.XUSB_BUTTON, button_mapped))
        elif self.controller_type == "ps4":
            self.gamepad.press_button(button=getattr(vg.DS4_BUTTONS, button_mapped))
        else:
            raise ValueError("Unsupported controller type")

    def release_button(self, button: str) -> None:
        if self.dry_run:
            return
        button_mapped = self.mapping.get(button)
        if self.controller_type == "xbox":
            self.gamepad.release_button(button=getattr(vg.XUSB_BUTTON, button_mapped))
        elif self.controller_type == "ps4":
            self.gamepad.release_button(button=getattr(vg.DS4_BUTTONS, button_mapped))
        else:
            raise ValueError("Unsupported controller type")

    def set_trigger(self, trigger: str, value: int) -> None:
        if self.dry_run:
            return
        trigger_mapped = self.mapping.get(trigger)
        if trigger_mapped == "LEFT_TRIGGER":
            self.gamepad.left_trigger(value=value)
        elif trigger_mapped == "RIGHT_TRIGGER":
            self.gamepad.right_trigger(value=value)
        else:
            raise ValueError("Unsupported trigger action")

    def set_joystick(self, joystick: str, value: int) -> None:
        if self.dry_run:
            return
        if joystick == "AXIS_LEFTX":
            self.left_joystick_x = value
            self.gamepad.left_joystick(x_value=self.left_joystick_x, y_value=self.left_joystick_y)
        elif joystick == "AXIS_LEFTY":
            if self.system == "windows":
                value = -value - 1
            self.left_joystick_y = value
            self.gamepad.left_joystick(x_value=self.left_joystick_x, y_value=self.left_joystick_y)
        elif joystick == "AXIS_RIGHTX":
            self.right_joystick_x = value
            self.gamepad.right_joystick(x_value=self.right_joystick_x, y_value=self.right_joystick_y)
        elif joystick == "AXIS_RIGHTY":
            if self.system == "windows":
                value = -value - 1
            self.right_joystick_y = value
            self.gamepad.right_joystick(x_value=self.right_joystick_x, y_value=self.right_joystick_y)
        else:
            raise ValueError("Unsupported joystick action")

    def wakeup(self, duration: float = 0.1) -> None:
        if self.dry_run:
            return
        self.gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB)
        self.gamepad.update()
        import time
        time.sleep(duration)
        self.gamepad.reset()
        self.gamepad.update()
        time.sleep(duration)

    def reset(self) -> None:
        if self.dry_run:
            return
        self.gamepad.reset()
        self.gamepad.update()
