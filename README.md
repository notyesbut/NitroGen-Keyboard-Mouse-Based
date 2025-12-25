# NitroGen (Safe KM Runtime)

NitroGen is a VLM-based game agent. This fork focuses on Windows stability, safe-by-default input, and keyboard+mouse (KM) control with an adapter so existing gamepad-trained models can run immediately.

## What this fork adds

- Safe-by-default runtime (no speedhack, no process injection)
- Keyboard+mouse controller via Win32 SendInput
- Gamepad->KM adapter so current checkpoints run without retraining
- Raw-input KM recording pipeline for future KM training
- Dry-run mode, rate-limited input, and STOP-file kill switch
- Interactive process picker with live-search

## Requirements

- Windows 10/11
- Python 3.10
- Your game installed locally (this repo does not ship games)
- CUDA-capable GPU recommended for inference server

## Install

```bash
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip setuptools wheel

pip install -e .[serve]
pip install -e .[play]
```

## Configure

Copy `.env.example` to `.env` and edit paths/values:

```bash
copy .env.example .env
```

## Run

Start the model server:

```bash
python scripts/serve.py
```

Run the agent:

```bash
python scripts/play.py --controller km
```

If you do not know the exact process name, use:

```bash
python scripts/play.py --pick-process
```

The model outputs gamepad actions. In KM mode, actions are mapped to WASD + mouse to provide immediate control. For best results, record KM demos and train a KM action head.

## Record KM demonstrations

```bash
python scripts/record_km.py --process Game.exe --fps 30
```

Outputs:
- `out/record_km/<run_id>/frames/*.png`
- `out/record_km/<run_id>/actions.jsonl`
- `out/record_km/<run_id>/meta.json`

Raw input mouse capture is enabled by default; use `--no-raw-mouse` if you need to fall back to cursor deltas.

## Safety notes

- Safe mode is default: no xspeedhack import and no process injection.
- Enable speedhack only if you accept the risks: `NG_ENABLE_SPEEDHACK=1`.
- Use `NG_DISABLE_INPUT=1` for dry-run (no input sent).
- Create a STOP file to break the loop immediately (default: `PATH_REPO/STOP`).

## Configuration details

See `RUNNING.md` for full env var and CLI options.
