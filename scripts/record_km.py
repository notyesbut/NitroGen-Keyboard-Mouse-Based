"""
Record keyboard+mouse actions with matching frames for KM training data.
"""

from __future__ import annotations

import os
import time
import json
import argparse
from pathlib import Path
from typing import Optional

import numpy as np

from nitrogen.game_env import GameEnv
from nitrogen.shared import PATH_REPO
from nitrogen.inference_viz import VideoRecorder
from nitrogen.input.keymap import (
    DEFAULT_KM_KEYS,
    DEFAULT_MOUSE_BUTTONS,
    parse_key_list,
    parse_mouse_button_list,
)
from nitrogen.input.keyboard_mouse_state import KeyboardMouseState
from nitrogen.input.raw_input import RawMouseHook
from nitrogen.process_picker import choose_process_name, process_exists, process_has_window


def load_dotenv_if_available() -> None:
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


def next_run_dir(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d_%H%M%S")
    path = base / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def parse_args() -> argparse.Namespace:
    load_dotenv_if_available()

    parser = argparse.ArgumentParser(description="Record KM actions + frames.")
    parser.add_argument(
        "--process",
        type=str,
        default=os.getenv("NG_PROCESS", "celeste.exe"),
        help="Game executable name (default: NG_PROCESS or celeste.exe)",
    )
    parser.add_argument(
        "--pick-process",
        action="store_true",
        default=env_flag("NG_PICK_PROCESS", False),
        help="Open a process selection menu at startup.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=int(os.getenv("NG_RECORD_FPS", "30")),
        help="Recording FPS (default: NG_RECORD_FPS or 30)",
    )
    parser.add_argument(
        "--image-width",
        type=int,
        default=int(os.getenv("NG_IMAGE_WIDTH", "2560")),
        help="Recorded frame width (default: NG_IMAGE_WIDTH or 2560)",
    )
    parser.add_argument(
        "--image-height",
        type=int,
        default=int(os.getenv("NG_IMAGE_HEIGHT", "1440")),
        help="Recorded frame height (default: NG_IMAGE_HEIGHT or 1440)",
    )
    parser.add_argument(
        "--screenshot-backend",
        type=str,
        default=os.getenv("NG_SCREENSHOT_BACKEND", "dxcam"),
        choices=["dxcam", "pyautogui"],
        help="Screenshot backend (default: NG_SCREENSHOT_BACKEND or dxcam)",
    )
    parser.add_argument(
        "--keys",
        type=str,
        default=os.getenv("NG_KM_KEYS"),
        help="Comma/space-separated key list to record.",
    )
    parser.add_argument(
        "--mouse-buttons",
        type=str,
        default=os.getenv("NG_KM_MOUSE_BUTTONS"),
        help="Comma/space-separated mouse buttons to record (left,right,middle,x1,x2).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(PATH_REPO / "out" / "record_km"),
        help="Base output directory (default: PATH_REPO/out/record_km)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=int(os.getenv("NG_RECORD_MAX_FRAMES", "0")),
        help="Max frames to record (0 = unlimited).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=float(os.getenv("NG_RECORD_DURATION", "0")),
        help="Max seconds to record (0 = unlimited).",
    )
    parser.add_argument(
        "--stop-file",
        type=str,
        default=os.getenv("NG_STOP_FILE", str(PATH_REPO / "STOP")),
        help="Stop recording when this file exists.",
    )
    raw_group = parser.add_mutually_exclusive_group()
    raw_group.add_argument(
        "--raw-mouse",
        dest="raw_mouse",
        action="store_true",
        help="Use raw input for mouse movement and wheel.",
    )
    raw_group.add_argument(
        "--no-raw-mouse",
        dest="raw_mouse",
        action="store_false",
        help="Disable raw input (uses cursor deltas).",
    )
    parser.set_defaults(raw_mouse=env_flag("NG_RECORD_RAW_MOUSE", True))

    focus_group = parser.add_mutually_exclusive_group()
    focus_group.add_argument(
        "--raw-focus-only",
        dest="raw_focus_only",
        action="store_true",
        help="Record raw mouse only while the game window is focused.",
    )
    focus_group.add_argument(
        "--raw-allow-background",
        dest="raw_focus_only",
        action="store_false",
        help="Allow raw mouse capture even when the game window is not focused.",
    )
    parser.set_defaults(raw_focus_only=env_flag("NG_RECORD_RAW_FOCUS_ONLY", True))
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="Do not save PNG frames (actions still recorded).",
    )
    parser.add_argument(
        "--video",
        action="store_true",
        help="Also write an MP4 preview.",
    )
    parser.add_argument(
        "--video-crf",
        type=int,
        default=int(os.getenv("NG_RECORD_CRF", "28")),
        help="CRF for preview video (default: NG_RECORD_CRF or 28).",
    )
    parser.add_argument(
        "--preset",
        type=str,
        default=os.getenv("NG_FFMPEG_PRESET", "medium"),
        help="FFmpeg preset for preview video (default: NG_FFMPEG_PRESET or medium).",
    )
    parser.add_argument(
        "--warmup-countdown",
        type=int,
        default=int(os.getenv("NG_WARMUP_COUNTDOWN", "3")),
        help="Countdown seconds before start (default: NG_WARMUP_COUNTDOWN or 3).",
    )

    return parser.parse_args()


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

    key_list = parse_key_list(args.keys, DEFAULT_KM_KEYS)
    mouse_buttons = parse_mouse_button_list(args.mouse_buttons, DEFAULT_MOUSE_BUTTONS)

    out_base = Path(args.out).expanduser().resolve()
    run_dir = next_run_dir(out_base)
    frames_dir = run_dir / "frames"
    if not args.no_png:
        frames_dir.mkdir(parents=True, exist_ok=True)
    actions_path = run_dir / "actions.jsonl"
    meta_path = run_dir / "meta.json"
    video_path = run_dir / "preview.mp4"

    stop_file = Path(args.stop_file).expanduser()
    fps = int(args.fps)
    step_s = 1.0 / max(1, fps)

    env = GameEnv(
        game=args.process,
        image_width=int(args.image_width),
        image_height=int(args.image_height),
        controller="km",
        disable_input=True,
        env_fps=fps,
        async_mode=False,
        screenshot_backend=args.screenshot_backend,
    )

    meta = {
        "process": args.process,
        "window_title": env.game_window_name,
        "fps": fps,
        "image_width": int(args.image_width),
        "image_height": int(args.image_height),
        "keys": key_list,
        "mouse_buttons": mouse_buttons,
        "screenshot_backend": args.screenshot_backend,
        "frames_saved": not args.no_png,
        "raw_mouse": bool(args.raw_mouse),
        "raw_focus_only": bool(args.raw_focus_only),
        "created_at": time.time(),
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    raw_mouse = None
    if args.raw_mouse:
        try:
            raw_mouse = RawMouseHook(
                capture_background=True,
                require_focus=bool(args.raw_focus_only),
                focus_pid=env.game_pid,
            )
            raw_mouse.start()
        except Exception as exc:
            print(f"Raw mouse input unavailable, falling back to cursor deltas: {exc}")
            raw_mouse = None

    km_state = KeyboardMouseState(keys=key_list, mouse_buttons=mouse_buttons, raw_mouse=raw_mouse)

    print(f"Recording to {run_dir}")
    print(f"Keys tracked: {len(key_list)}")
    print(f"Mouse buttons tracked: {mouse_buttons}")
    print("Starting recording...")
    for i in range(args.warmup_countdown, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    start_time = time.time()
    frame_idx = 0
    next_tick = time.perf_counter()
    max_frames = int(args.max_frames)
    duration = float(args.duration)

    recorder: Optional[VideoRecorder] = None
    if args.video:
        recorder = VideoRecorder(str(video_path), fps=fps, crf=int(args.video_crf), preset=args.preset)

    try:
        with open(actions_path, "a", buffering=1) as f_actions:
            while True:
                if stop_file.exists():
                    print(f"Stop file detected at {stop_file}. Stopping.")
                    break
                if max_frames > 0 and frame_idx >= max_frames:
                    break
                if duration > 0 and (time.time() - start_time) >= duration:
                    break

                action = km_state.sample()
                action["frame_index"] = frame_idx

                frame = env.render()

                if not args.no_png:
                    frame_path = frames_dir / f"{frame_idx:06d}.png"
                    frame.save(frame_path)
                    action["frame"] = str(frame_path.relative_to(run_dir))

                if recorder is not None:
                    recorder.add_frame(np.array(frame))

                json.dump(action, f_actions)
                f_actions.write("\n")

                frame_idx += 1
                next_tick += step_s
                sleep_s = next_tick - time.perf_counter()
                if sleep_s > 0:
                    time.sleep(sleep_s)

    finally:
        if raw_mouse is not None:
            raw_mouse.stop()
        if recorder is not None:
            recorder.close()
        env.close()

    print(f"Done. Recorded {frame_idx} frames.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
