"""
NitroGen agent runner (play)

Key improvements vs original:
- Clean `main()` entrypoint and structured functions
- Optional .env support (python-dotenv) without hard dependency
- ENV defaults for process/port and other knobs
- Correct, explicit dtypes for gamepad actions (int16 for axes, uint8 for triggers)
- Safe action template copying (no shared numpy references)
- JSONL logging without mutating in-memory actions
- Optional PNG debug saving with interval (prevents disk explosion)
- Deduplicated “menu init” flow for special games
- Better error handling and clean shutdown
"""

from __future__ import annotations

import os
import time
import json
import argparse
from pathlib import Path
from collections import OrderedDict
from typing import Any, Dict, Iterable, Tuple

import cv2
import numpy as np
from PIL import Image

from nitrogen.game_env import GameEnv
from nitrogen.action_adapters.gamepad_to_km import gamepad_action_to_km
from nitrogen.process_picker import choose_process_name, process_exists, process_has_window
from nitrogen.shared import BUTTON_ACTION_TOKENS, PATH_REPO
from nitrogen.inference_viz import create_viz, VideoRecorder
from nitrogen.inference_client import ModelClient


# -----------------------------
# Optional .env loading
# -----------------------------
def load_dotenv_if_available() -> None:
    """
    If python-dotenv is installed and a .env exists, load it.
    This is optional and does not break environments without python-dotenv.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except Exception:
        pass


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


# -----------------------------
# Image pre-processing
# -----------------------------
def preprocess_img(main_image: Image.Image) -> Image.Image:
    """
    Convert PIL->OpenCV, resize to 256x256, return PIL RGB.
    """
    main_cv = cv2.cvtColor(np.array(main_image), cv2.COLOR_RGB2BGR)
    final_image = cv2.resize(main_cv, (256, 256), interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(final_image, cv2.COLOR_BGR2RGB))


# -----------------------------
# Gamepad action handling
# -----------------------------
AXIS_SCALE = 32767
TRIGGER_SCALE = 255

AXIS_DTYPE = np.int16
TRIGGER_DTYPE = np.uint8


def action_template() -> "OrderedDict[str, Any]":
    """
    Create a fresh action template for each action step.
    Use explicit dtypes to match typical controller expectations:
      - axes: int16 in range [-32768..32767]
      - triggers: uint8 in range [0..255]
    """
    return OrderedDict(
        [
            ("WEST", 0),
            ("SOUTH", 0),
            ("BACK", 0),
            ("DPAD_DOWN", 0),
            ("DPAD_LEFT", 0),
            ("DPAD_RIGHT", 0),
            ("DPAD_UP", 0),
            ("GUIDE", 0),
            ("AXIS_LEFTX", np.array([0], dtype=AXIS_DTYPE)),
            ("AXIS_LEFTY", np.array([0], dtype=AXIS_DTYPE)),
            ("LEFT_SHOULDER", 0),
            ("LEFT_TRIGGER", np.array([0], dtype=TRIGGER_DTYPE)),
            ("AXIS_RIGHTX", np.array([0], dtype=AXIS_DTYPE)),
            ("AXIS_RIGHTY", np.array([0], dtype=AXIS_DTYPE)),
            ("LEFT_THUMB", 0),
            ("RIGHT_THUMB", 0),
            ("RIGHT_SHOULDER", 0),
            ("RIGHT_TRIGGER", np.array([0], dtype=TRIGGER_DTYPE)),
            ("START", 0),
            ("EAST", 0),
            ("NORTH", 0),
        ]
    )


def clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def clamp_axis(x: float) -> int:
    # The model likely returns [-1..1]. Clip aggressively.
    if x < -1.0:
        x = -1.0
    if x > 1.0:
        x = 1.0
    return int(x * AXIS_SCALE)


def build_env_actions(
    pred: Dict[str, Any],
    token_set: Iterable[str],
    button_press_thres: float,
) -> list["OrderedDict[str, Any]"]:
    """
    Convert model prediction into a list of gamepad actions.
    Expected pred keys: "j_left", "j_right", "buttons"
      - j_left: list[[xl, yl], ...] floats in [-1..1]
      - j_right: list[[xr, yr], ...] floats in [-1..1]
      - buttons: list[list[float], ...] floats in [0..1] (or logits already scaled)
    """
    j_left = pred.get("j_left")
    j_right = pred.get("j_right")
    buttons = pred.get("buttons")

    if j_left is None or j_right is None or buttons is None:
        raise ValueError("Model prediction missing required keys: j_left/j_right/buttons")

    n = len(buttons)
    if n != len(j_left) or n != len(j_right):
        raise ValueError("Mismatch in action lengths: buttons vs j_left vs j_right")

    token_list = list(token_set)

    actions: list[OrderedDict[str, Any]] = []

    for i in range(n):
        a = action_template()

        xl, yl = j_left[i]
        xr, yr = j_right[i]

        a["AXIS_LEFTX"] = np.array([clamp_axis(float(xl))], dtype=AXIS_DTYPE)
        a["AXIS_LEFTY"] = np.array([clamp_axis(float(yl))], dtype=AXIS_DTYPE)
        a["AXIS_RIGHTX"] = np.array([clamp_axis(float(xr))], dtype=AXIS_DTYPE)
        a["AXIS_RIGHTY"] = np.array([clamp_axis(float(yr))], dtype=AXIS_DTYPE)

        button_vector = buttons[i]
        if len(button_vector) != len(token_list):
            raise ValueError("Button vector length does not match token set length")

        for name, value in zip(token_list, button_vector):
            v = float(value)
            if "TRIGGER" in name:
                a[name] = np.array([int(clamp01(v) * TRIGGER_SCALE)], dtype=TRIGGER_DTYPE)
            else:
                a[name] = 1 if v > button_press_thres else 0

        actions.append(a)

    return actions


def sanitize_menu_actions(a: "OrderedDict[str, Any]") -> None:
    """
    Remove potentially disruptive menu/system actions.
    Mutates the action dict.
    """
    a["GUIDE"] = 0
    a["START"] = 0
    a["BACK"] = 0


# -----------------------------
# Paths / outputs
# -----------------------------
def next_run_number(path_out: Path) -> int:
    """
    Find next available run number from files like 0001_DEBUG.mp4.
    """
    video_files = sorted(path_out.glob("*_DEBUG.mp4"))
    if not video_files:
        return 1

    nums: list[int] = []
    for f in video_files:
        prefix = f.name.split("_")[0]
        if prefix.isdigit():
            nums.append(int(prefix))
    return (max(nums) + 1) if nums else 1


def ensure_dirs(path_repo: Path, ckpt_name: str) -> Tuple[Path, Path, Path, Path, Path]:
    """
    Returns:
      PATH_DEBUG, PATH_OUT, PATH_MP4_DEBUG, PATH_MP4_CLEAN, PATH_ACTIONS
    """
    path_debug = path_repo / "debug"
    path_debug.mkdir(parents=True, exist_ok=True)

    path_out = (path_repo / "out" / ckpt_name).resolve()
    path_out.mkdir(parents=True, exist_ok=True)

    run_no = next_run_number(path_out)

    path_mp4_debug = path_out / f"{run_no:04d}_DEBUG.mp4"
    path_mp4_clean = path_out / f"{run_no:04d}_CLEAN.mp4"
    path_actions = path_out / f"{run_no:04d}_ACTIONS.jsonl"

    return path_debug, path_out, path_mp4_debug, path_mp4_clean, path_actions


def json_ready_action(action: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert numpy arrays to lists for JSON serialization without mutating original.
    """
    out: Dict[str, Any] = {}
    for k, v in action.items():
        if isinstance(v, np.ndarray):
            out[k] = v.tolist()
        else:
            out[k] = v
    return out


# -----------------------------
# Game-specific init helpers
# -----------------------------
def press_button(env: GameEnv, button: str, hold_s: float = 0.05) -> None:
    controller = getattr(env, "controller", None)
    if controller is None or not hasattr(controller, "press_button"):
        return
    controller.press_button(button)
    if hasattr(controller, "gamepad"):
        controller.gamepad.update()
    time.sleep(hold_s)
    controller.release_button(button)
    if hasattr(controller, "gamepad"):
        controller.gamepad.update()


def maybe_initialize_controller_menu(env: GameEnv, process_name: str) -> None:
    """
    Some games require a menu navigation to "activate" the virtual controller.
    Extend this mapping if needed.
    """
    if getattr(env, "controller_kind", None) != "gamepad":
        return
    if getattr(env, "disable_input", False):
        return

    process_norm = process_name.lower()

    # Add games here that need this init flow.
    needs_init = {
        "isaac-ng.exe",
        "cuphead.exe",
    }

    if process_norm not in needs_init:
        return

    print(f"Gamepad controller ready for {process_name} at {env.env_fps} FPS")
    input("Press Enter to create a virtual controller and start rollouts...")

    for i in range(3):
        print(f"{3 - i}...")
        time.sleep(1)

    # Simple init sequence: SOUTH then several EAST presses.
    press_button(env, "SOUTH")
    for _ in range(5):
        press_button(env, "EAST")
        time.sleep(0.3)


# -----------------------------
# CLI
# -----------------------------
def parse_args() -> argparse.Namespace:
    load_dotenv_if_available()

    parser = argparse.ArgumentParser(description="NitroGen VLM Agent Runner")

    parser.add_argument(
        "--process",
        type=str,
        default=os.getenv("NG_PROCESS", "celeste.exe"),
        help="Exact game executable name (default: NG_PROCESS or celeste.exe)",
    )
    parser.add_argument(
        "--pick-process",
        action="store_true",
        default=env_flag("NG_PICK_PROCESS", False),
        help="Open a process selection menu at startup.",
    )


def km_action_template() -> Dict[str, Any]:
    return {
        "keys": [],
        "mouse_dx": 0,
        "mouse_dy": 0,
        "mouse_buttons": [],
        "mouse_wheel": 0,
    }
    parser.add_argument(
        "--controller",
        type=str,
        default=os.getenv("NG_CONTROLLER", "gamepad").lower(),
        choices=["gamepad", "km"],
        help="Input controller: gamepad or km (default: NG_CONTROLLER or gamepad)",
    )
    parser.add_argument(
        "--controller-type",
        type=str,
        default=os.getenv("NG_GAMEPAD_TYPE", "xbox"),
        choices=["xbox", "ps4"],
        help="Gamepad type for gamepad controller (default: NG_GAMEPAD_TYPE or xbox)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("NG_PORT", "5555")),
        help="Port for model server (default: NG_PORT or 5555)",
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--disable-input",
        dest="disable_input",
        action="store_true",
        help="Disable sending input (dry-run). Default: NG_DISABLE_INPUT or False.",
    )
    input_group.add_argument(
        "--enable-input",
        dest="disable_input",
        action="store_false",
        help="Enable input if it was disabled by env.",
    )
    parser.set_defaults(disable_input=env_flag("NG_DISABLE_INPUT", False))

    speed_group = parser.add_mutually_exclusive_group()
    speed_group.add_argument(
        "--enable-speedhack",
        dest="enable_speedhack",
        action="store_true",
        help="Enable xspeedhack (unsafe). Default: NG_ENABLE_SPEEDHACK or False.",
    )
    speed_group.add_argument(
        "--disable-speedhack",
        dest="enable_speedhack",
        action="store_false",
        help="Disable xspeedhack even if enabled by env.",
    )
    parser.set_defaults(enable_speedhack=env_flag("NG_ENABLE_SPEEDHACK", False))
    parser.add_argument(
        "--stop-file",
        type=str,
        default=os.getenv("NG_STOP_FILE", str(PATH_REPO / "STOP")),
        help="Path to stop file that terminates the loop if present.",
    )
    parser.add_argument(
        "--km-mouse-sens",
        type=float,
        default=float(os.getenv("NG_KM_MOUSE_SENS", "15")),
        help="Mouse sensitivity (pixels per step) for gamepad->KM adapter.",
    )
    parser.add_argument(
        "--km-deadzone",
        type=float,
        default=float(os.getenv("NG_KM_DEADZONE", "0.2")),
        help="Axis deadzone for gamepad->KM adapter.",
    )
    parser.add_argument(
        "--km-mouse-max",
        type=int,
        default=int(os.getenv("NG_KM_MOUSE_MAX", "50")),
        help="Max mouse delta per step for gamepad->KM adapter.",
    )
    parser.add_argument(
        "--km-trigger-thres",
        type=float,
        default=float(os.getenv("NG_KM_TRIGGER_THRES", "0.1")),
        help="Trigger press threshold for gamepad->KM adapter.",
    )
    parser.add_argument(
        "--allow-menu",
        action="store_true",
        help="Allow menu/system actions (GUIDE/START/BACK). Disabled by default.",
    )
    parser.add_argument(
        "--env-fps",
        type=int,
        default=int(os.getenv("NG_ENV_FPS", "60")),
        help="Environment FPS (default: NG_ENV_FPS or 60)",
    )
    parser.add_argument(
        "--game-speed",
        type=float,
        default=float(os.getenv("NG_GAME_SPEED", "1.0")),
        help="Game speed multiplier if supported (default: NG_GAME_SPEED or 1.0)",
    )
    parser.add_argument(
        "--button-thres",
        type=float,
        default=float(os.getenv("NG_BUTTON_THRES", "0.5")),
        help="Button press threshold (default: NG_BUTTON_THRES or 0.5)",
    )
    parser.add_argument(
        "--debug-png",
        action="store_true",
        help="Save per-step PNGs to PATH_REPO/debug (can be large).",
    )
    parser.add_argument(
        "--debug-png-every",
        type=int,
        default=int(os.getenv("NG_DEBUG_PNG_EVERY", "1")),
        help="Save debug PNG every N steps (default: NG_DEBUG_PNG_EVERY or 1)",
    )
    parser.add_argument(
        "--debug-crf",
        type=int,
        default=int(os.getenv("NG_DEBUG_CRF", "32")),
        help="CRF for debug video (default: NG_DEBUG_CRF or 32)",
    )
    parser.add_argument(
        "--clean-crf",
        type=int,
        default=int(os.getenv("NG_CLEAN_CRF", "28")),
        help="CRF for clean video (default: NG_CLEAN_CRF or 28)",
    )
    parser.add_argument(
        "--preset",
        type=str,
        default=os.getenv("NG_FFMPEG_PRESET", "medium"),
        help="FFmpeg preset for videos (default: NG_FFMPEG_PRESET or medium)",
    )
    parser.add_argument(
        "--warmup-countdown",
        type=int,
        default=int(os.getenv("NG_WARMUP_COUNTDOWN", "3")),
        help="Countdown seconds before start (default: NG_WARMUP_COUNTDOWN or 3)",
    )

    return parser.parse_args()


# -----------------------------
# Main loop
# -----------------------------
def main() -> int:
    args = parse_args()

    process_ok = process_exists(args.process)
    process_ready = process_has_window(args.process)
    if args.pick_process or not process_ready:
        if not process_ok:
            print(f"Process not found: {args.process}")
        elif not process_ready:
            print(f"Process has no visible window yet: {args.process}")
        args.process = choose_process_name(
            default_name=args.process,
            show_all_default=env_flag("NG_PICK_PROCESS_ALL", False),
            live_search=env_flag("NG_PICK_PROCESS_LIVE", True),
            max_rows=int(os.getenv("NG_PICK_PROCESS_MAX_ROWS", "30")),
        )

    # Connect to model server
    policy = ModelClient(port=args.port)

    try:
        policy.reset()
        policy_info = policy.info()
    except Exception as e:
        print(f"Failed to connect to model server on port {args.port}: {e}")
        print("Make sure you started the server, e.g.: python scripts/serve.py (or with ckpt path).")
        return 2

    action_downsample_ratio = int(policy_info.get("action_downsample_ratio", 1))
    ckpt_path = str(policy_info.get("ckpt_path", "unknown"))
    ckpt_name = Path(ckpt_path).stem if ckpt_path and ckpt_path != "unknown" else "unknown_ckpt"

    no_menu = not args.allow_menu

    # Output paths
    path_debug, path_out, path_mp4_debug, path_mp4_clean, path_actions = ensure_dirs(PATH_REPO, ckpt_name)

    print("Model client ready.")
    print(f"Server port: {args.port}")
    print(f"Checkpoint: {ckpt_path}")
    print(f"action_downsample_ratio: {action_downsample_ratio}")
    print(f"Output dir: {path_out}")
    print(f"Debug video: {path_mp4_debug.name}")
    print(f"Clean video: {path_mp4_clean.name}")
    print(f"Actions log: {path_actions.name}")
    print(f"Controller: {args.controller}")
    print(f"Dry-run input: {args.disable_input}")
    if args.enable_speedhack:
        print("Speedhack: ENABLED (unsafe)")

    # Countdown before starting environment
    print("Starting environment...")
    for i in range(args.warmup_countdown, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    stop_file = Path(args.stop_file)

    env = GameEnv(
        game=args.process,
        game_speed=float(args.game_speed),
        env_fps=int(args.env_fps),
        async_mode=True,
        controller=args.controller,
        controller_type=args.controller_type,
        enable_speedhack=args.enable_speedhack,
        disable_input=args.disable_input,
    )

    try:
        maybe_initialize_controller_menu(env, args.process)

        env.reset()
        env.pause()

        # Initial call to get state
        zero = action_template() if args.controller == "gamepad" else km_action_template()
        obs, reward, terminated, truncated, info = env.step(action=zero)

        step_count = 0
        token_set = BUTTON_ACTION_TOKENS

        with open(path_actions, "a", buffering=1) as f_actions:
            with VideoRecorder(str(path_mp4_debug), fps=int(args.env_fps), crf=int(args.debug_crf), preset=args.preset) as debug_recorder:
                with VideoRecorder(str(path_mp4_clean), fps=int(args.env_fps), crf=int(args.clean_crf), preset=args.preset) as clean_recorder:
                    try:
                        while True:
                            if stop_file.exists():
                                print(f"Stop file detected at {stop_file}. Exiting loop.")
                                break

                            # Preprocess observation for model
                            obs_pil = preprocess_img(obs)

                            # Optional PNG debug saving (throttled)
                            if args.debug_png and (step_count % max(1, args.debug_png_every) == 0):
                                obs_pil.save(path_debug / f"{step_count:05d}.png")

                            # Predict action plan
                            pred = policy.predict(obs_pil)

                            # Build actions
                            env_actions = build_env_actions(
                                pred=pred,
                                token_set=token_set,
                                button_press_thres=float(args.button_thres),
                            )

                            exec_actions = []
                            for a in env_actions:
                                if no_menu:
                                    if a.get("START") == 1:
                                        print("Model predicted START; disabled (allow with --allow-menu).")
                                    sanitize_menu_actions(a)

                                if args.controller == "km":
                                    km_action = gamepad_action_to_km(
                                        a,
                                        mouse_sens=float(args.km_mouse_sens),
                                        axis_deadzone=float(args.km_deadzone),
                                        mouse_max=int(args.km_mouse_max),
                                        trigger_threshold=float(args.km_trigger_thres),
                                    )
                                    exec_actions.append(km_action)
                                else:
                                    exec_actions.append(a)

                            # Execute actions
                            print(
                                f"Executing {len(exec_actions)} actions; each repeated {action_downsample_ratio} times"
                            )

                            stop_requested = False
                            for sub_i, a in enumerate(exec_actions):
                                if stop_file.exists():
                                    print(f"Stop file detected at {stop_file}. Exiting loop.")
                                    stop_requested = True
                                    break

                                for _ in range(action_downsample_ratio):
                                    if stop_file.exists():
                                        print(f"Stop file detected at {stop_file}. Exiting loop.")
                                        stop_requested = True
                                        break
                                    obs, reward, terminated, truncated, info = env.step(action=a)

                                    # Visualization frames
                                    obs_viz = np.array(obs).copy()

                                    clean_viz = cv2.resize(obs_viz, (1920, 1080), interpolation=cv2.INTER_AREA)
                                    debug_viz = create_viz(
                                        cv2.resize(obs_viz, (1280, 720), interpolation=cv2.INTER_AREA),
                                        sub_i,
                                        pred["j_left"],
                                        pred["j_right"],
                                        pred["buttons"],
                                        token_set=token_set,
                                    )

                                    debug_recorder.add_frame(debug_viz)
                                    clean_recorder.add_frame(clean_viz)

                                if stop_requested:
                                    break

                                # Log the executed action as JSONL (without mutating `a`)
                                record = json_ready_action(a)
                                record["step"] = step_count
                                record["substep"] = sub_i
                                json.dump(record, f_actions)
                                f_actions.write("\n")

                            step_count += 1
                            if stop_requested:
                                break

                    finally:
                        env.unpause()

    except KeyboardInterrupt:
        print("\nInterrupted. Shutting down cleanly...")
        return 0
    except Exception as e:
        print(f"\nFatal error: {e}")
        return 1
    finally:
        try:
            env.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
