# Running NitroGen (Windows)

## Quick start

1) Start the inference server (uses NG_PT or PATH_TO_NG if set):

```bash
python scripts/serve.py
```

2) Run the agent:

```bash
python scripts/play.py --controller km
```

`--controller` can be `gamepad` or `km`.

## .env / environment variables

Recommended `.env` example:

```bash
NG_PT=C:\path\to\ng.pt
NG_PORT=5555
NG_PROCESS=Game.exe
NG_CONTROLLER=km
NG_KM_MOUSE_SENS=15
```

Supported variables:

- `NG_PT`: full path to `ng.pt`.
- `PATH_TO_NG`: directory containing `ng.pt`.
- `NG_PORT`: inference server port.
- `NG_PROCESS`: game executable name (e.g. `Game.exe`).
- `NG_CONTROLLER`: `gamepad` or `km`.
- `NG_KM_MOUSE_SENS`: mouse sensitivity (pixels per step) for the gamepad->KM adapter.
- `NG_KM_DEADZONE`: deadzone for stick -> WASD mapping (default `0.2`).
- `NG_KM_MOUSE_MAX`: max mouse delta per step (default `50`).
- `NG_KM_TRIGGER_THRES`: trigger press threshold (default `0.1`).
- `NG_DISABLE_INPUT`: set to `1` to run in dry-run mode (no input is sent).
- `NG_STOP_FILE`: path to a stop file. If it exists, `play.py` exits the loop.
- `NG_ENABLE_SPEEDHACK`: set to `1` to enable xspeedhack (unsafe, off by default).

## Safety notes

- Safe mode is the default: no xspeedhack import, no process injection, only OS-level input and screen capture.
- Enabling `NG_ENABLE_SPEEDHACK=1` uses `xspeedhack` and is unsafe for anti-cheat.
- Prefer offline / non-competitive environments.

## KM adapter notes

The current model outputs gamepad actions. For KM control, `play.py` maps gamepad actions to keyboard/mouse (WASD + mouse) so it can run immediately. For best results, collect KM demonstrations and train a KM action head.
