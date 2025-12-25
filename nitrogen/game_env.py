from __future__ import annotations

import os
import time
import platform
from typing import Any, Mapping

import numpy as np
import psutil
import pywinctl as pwc
from gymnasium import Env
from gymnasium.spaces import Box, Dict, Discrete, MultiBinary
from PIL import Image

from nitrogen.input.base import InputController

assert platform.system().lower() == "windows", "This module is only supported on Windows."

import win32process
import win32gui
import win32api
import win32con


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_process_info(process_name: str) -> dict:
    """
    Get process information for a given process name on Windows.

    Args:
        process_name (str): Name of the process (e.g., "isaac-ng.exe")

    Returns:
        dict: Dictionary containing PID, window_name, and architecture
              for the first matching process.
    """
    results = []

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"].lower() == process_name.lower():
                pid = proc.info["pid"]

                try:
                    process_handle = win32api.OpenProcess(
                        win32con.PROCESS_QUERY_INFORMATION,
                        False,
                        pid,
                    )
                    is_wow64 = win32process.IsWow64Process(process_handle)
                    win32api.CloseHandle(process_handle)
                    architecture = "x86" if is_wow64 else "x64"
                except Exception:
                    architecture = "unknown"

                windows = []

                def enum_window_callback(hwnd, pid_to_find):
                    _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
                    if found_pid == pid_to_find:
                        window_text = win32gui.GetWindowText(hwnd)
                        if window_text and win32gui.IsWindowVisible(hwnd):
                            windows.append({
                                "hwnd": hwnd,
                                "title": window_text,
                                "visible": win32gui.IsWindowVisible(hwnd),
                            })
                    return True

                try:
                    win32gui.EnumWindows(enum_window_callback, pid)
                except Exception:
                    pass

                window_name = None
                if windows:
                    if len(windows) > 1:
                        print(f"Multiple windows found for PID {pid}: {[win['title'] for win in windows]}")
                        print("Using heuristics to select the correct window...")
                    proxy_keywords = ["d3dproxywindow", "proxy", "helper", "overlay"]
                    for win in windows:
                        if not any(keyword in win["title"].lower() for keyword in proxy_keywords):
                            window_name = win["title"]
                            break
                    if window_name is None and windows:
                        window_name = windows[0]["title"]

                results.append({
                    "pid": pid,
                    "window_name": window_name,
                    "architecture": architecture,
                })

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if len(results) == 0:
        raise ValueError(f"No process found with name: {process_name}")
    if len(results) > 1:
        print(f"Warning: Multiple processes found with name '{process_name}'. Returning first match.")

    return results[0]


class PyautoguiScreenshotBackend:
    def __init__(self, bbox: tuple[int, int, int, int]):
        import pyautogui
        self.pyautogui = pyautogui
        self.bbox = bbox

    def screenshot(self) -> Image.Image:
        return self.pyautogui.screenshot(region=self.bbox)

    def close(self) -> None:
        pass


class DxcamScreenshotBackend:
    def __init__(self, bbox: tuple[int, int, int, int], fps: int):
        import dxcam
        self.camera = dxcam.create()
        self.bbox = bbox
        self.last_screenshot = None
        self.camera.start(region=self.bbox, target_fps=fps, video_mode=True)

    def screenshot(self) -> Image.Image:
        screenshot = self.camera.get_latest_frame()
        if screenshot is None:
            print("DXCAM failed to capture frame, trying to use the latest screenshot")
            if self.last_screenshot is not None:
                return self.last_screenshot
            return Image.new("RGB", (self.bbox[2], self.bbox[3]), (0, 0, 0))
        screenshot = Image.fromarray(screenshot)
        self.last_screenshot = screenshot
        return screenshot

    def close(self) -> None:
        try:
            self.camera.stop()
        except Exception:
            pass


class GameEnv(Env):
    """
    Game environment with pluggable input controller.
    """

    def __init__(
        self,
        game: str,
        image_height: int = 1440,
        image_width: int = 2560,
        controller: str | InputController = "gamepad",
        controller_type: str = "xbox",
        game_speed: float = 1.0,
        env_fps: int = 10,
        async_mode: bool = True,
        screenshot_backend: str = "dxcam",
        enable_speedhack: bool | None = None,
        disable_input: bool | None = None,
    ) -> None:
        super().__init__()

        os_name = platform.system().lower()
        assert os_name == "windows", "This environment is currently only supported on Windows."
        assert screenshot_backend in ["pyautogui", "dxcam"], "Screenshot backend must be either 'pyautogui' or 'dxcam'"

        self.game = game
        self.image_height = int(image_height)
        self.image_width = int(image_width)
        self.game_speed = float(game_speed)
        self.env_fps = int(env_fps)
        self.step_duration = self.calculate_step_duration()
        self.async_mode = bool(async_mode)

        self.disable_input = disable_input if disable_input is not None else _env_flag("NG_DISABLE_INPUT", False)
        self.enable_speedhack = enable_speedhack if enable_speedhack is not None else _env_flag("NG_ENABLE_SPEEDHACK", False)

        self.controller_kind = "custom"
        if isinstance(controller, InputController):
            self.controller = controller
        else:
            controller_norm = controller.lower().strip()
            if controller_norm in {"gamepad", "pad"}:
                from nitrogen.input.gamepad import GamepadController
                self.controller = GamepadController(
                    controller_type=controller_type,
                    system=os_name,
                    dry_run=self.disable_input,
                )
                self.controller_kind = "gamepad"
            elif controller_norm in {"km", "keyboard", "keyboard_mouse"}:
                from nitrogen.input.keyboard_mouse import KeyboardMouseController
                self.controller = KeyboardMouseController(dry_run=self.disable_input)
                self.controller_kind = "km"
            else:
                raise ValueError(f"Unsupported controller type: {controller}")

        proc_info = get_process_info(game)
        self.game_pid = proc_info["pid"]
        self.game_arch = proc_info["architecture"]
        self.game_window_name = proc_info["window_name"]

        print(
            f"Game process found: {self.game} (PID: {self.game_pid}, Arch: {self.game_arch}, Window: {self.game_window_name})"
        )

        if self.game_pid is None:
            raise RuntimeError(f"Could not find PID for game: {game}")

        self.observation_space = Box(
            low=0,
            high=255,
            shape=(self.image_height, self.image_width, 3),
            dtype="uint8",
        )

        self.action_space = self._build_action_space()

        windows = pwc.getAllWindows()
        self.game_window = None
        for window in windows:
            if window.title == self.game_window_name:
                self.game_window = window
                break

        if not self.game_window:
            raise RuntimeError(f"No window found with game name: {self.game}")

        self.game_window.activate()
        l, t, r, b = self.game_window.left, self.game_window.top, self.game_window.right, self.game_window.bottom
        self.bbox = (l, t, r - l, b - t)

        self.speedhack_client = None
        if self.enable_speedhack:
            try:
                import xspeedhack as xsh
            except Exception as exc:
                raise ImportError(
                    "xspeedhack is required for speed control but is not installed. "
                    "Set NG_ENABLE_SPEEDHACK=0 to run in safe mode."
                ) from exc
            self.speedhack_client = xsh.Client(process_id=self.game_pid, arch=self.game_arch)

        if screenshot_backend == "dxcam":
            self.screenshot_backend = DxcamScreenshotBackend(self.bbox, self.env_fps)
        elif screenshot_backend == "pyautogui":
            self.screenshot_backend = PyautoguiScreenshotBackend(self.bbox)
        else:
            raise ValueError("Unsupported screenshot backend. Use 'dxcam' or 'pyautogui'.")

    def _build_action_space(self) -> Dict:
        if self.controller_kind == "km":
            from nitrogen.input.keyboard_mouse import DEFAULT_KEYS, DEFAULT_MOUSE_BUTTONS
            mouse_limit = int(os.getenv("NG_KM_MOUSE_MAX", "50"))
            return Dict(
                {
                    "keys": MultiBinary(len(DEFAULT_KEYS)),
                    "mouse_dx": Box(low=-mouse_limit, high=mouse_limit, shape=(1,), dtype=np.int16),
                    "mouse_dy": Box(low=-mouse_limit, high=mouse_limit, shape=(1,), dtype=np.int16),
                    "mouse_buttons": MultiBinary(len(DEFAULT_MOUSE_BUTTONS)),
                    "mouse_wheel": Box(low=-1200, high=1200, shape=(1,), dtype=np.int16),
                }
            )

        return Dict(
            {
                "BACK": Discrete(2),
                "GUIDE": Discrete(2),
                "RIGHT_SHOULDER": Discrete(2),
                "RIGHT_TRIGGER": Box(low=0.0, high=1.0, shape=(1,)),
                "LEFT_TRIGGER": Box(low=0.0, high=1.0, shape=(1,)),
                "LEFT_SHOULDER": Discrete(2),
                "AXIS_RIGHTX": Box(low=-32768.0, high=32767, shape=(1,)),
                "AXIS_RIGHTY": Box(low=-32768.0, high=32767, shape=(1,)),
                "AXIS_LEFTX": Box(low=-32768.0, high=32767, shape=(1,)),
                "AXIS_LEFTY": Box(low=-32768.0, high=32767, shape=(1,)),
                "LEFT_THUMB": Discrete(2),
                "RIGHT_THUMB": Discrete(2),
                "DPAD_UP": Discrete(2),
                "DPAD_RIGHT": Discrete(2),
                "DPAD_DOWN": Discrete(2),
                "DPAD_LEFT": Discrete(2),
                "WEST": Discrete(2),
                "SOUTH": Discrete(2),
                "EAST": Discrete(2),
                "NORTH": Discrete(2),
                "START": Discrete(2),
            }
        )

    def calculate_step_duration(self) -> float:
        return 1.0 / (self.env_fps * self.game_speed)

    def unpause(self) -> None:
        if self.speedhack_client is None:
            return
        self.speedhack_client.set_speed(1.0)

    def pause(self) -> None:
        if self.speedhack_client is None:
            return
        self.speedhack_client.set_speed(0.0)

    def perform_action(self, action: Mapping[str, Any], duration: float) -> None:
        self.controller.step(action)

        if self.enable_speedhack and self.async_mode:
            self.unpause()
            time.sleep(duration)
            self.pause()
        else:
            time.sleep(duration)

    def step(self, action: Mapping[str, Any], step_duration: float | None = None):
        duration = step_duration if step_duration is not None else self.step_duration
        self.perform_action(action, duration)

        obs = self.render()
        reward = 0.0
        terminated = False
        truncated = False
        info = {}
        return obs, reward, terminated, truncated, info

    def reset(self, seed: int | None = None, options: dict | None = None):
        if hasattr(self.controller, "wakeup"):
            self.controller.wakeup(duration=0.1)
        else:
            self.controller.reset()
        time.sleep(1.0)

    def close(self) -> None:
        try:
            self.controller.close()
        finally:
            if hasattr(self.screenshot_backend, "close"):
                try:
                    self.screenshot_backend.close()
                except Exception:
                    pass

    def render(self) -> Image.Image:
        screenshot = self.screenshot_backend.screenshot()
        screenshot = screenshot.resize((self.image_width, self.image_height))
        return screenshot


class GamepadEnv(GameEnv):
    def __init__(
        self,
        game: str,
        image_height: int = 1440,
        image_width: int = 2560,
        controller_type: str = "xbox",
        game_speed: float = 1.0,
        env_fps: int = 10,
        async_mode: bool = True,
        screenshot_backend: str = "dxcam",
        enable_speedhack: bool | None = None,
        disable_input: bool | None = None,
    ) -> None:
        super().__init__(
            game=game,
            image_height=image_height,
            image_width=image_width,
            controller="gamepad",
            controller_type=controller_type,
            game_speed=game_speed,
            env_fps=env_fps,
            async_mode=async_mode,
            screenshot_backend=screenshot_backend,
            enable_speedhack=enable_speedhack,
            disable_input=disable_input,
        )
