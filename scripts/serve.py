import os
import argparse
import pickle
from pathlib import Path

import zmq

from nitrogen.inference_session import InferenceSession


def _load_dotenv_if_available() -> None:
    """
    Optional: allow a local .env file without hard dependency.
    If python-dotenv isn't installed, this is a no-op.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except Exception:
        pass


def _resolve_ckpt_path(cli_ckpt: str | None) -> str:
    """
    Resolution order:
      1) CLI positional ckpt
      2) NG_PT env var (full path to ng.pt)
      3) PATH_TO_NG env var (directory containing ng.pt) -> PATH_TO_NG/ng.pt
    """
    if cli_ckpt:
        p = Path(cli_ckpt).expanduser()
        return str(p)

    ng_pt = os.getenv("NG_PT")
    if ng_pt:
        return str(Path(ng_pt).expanduser())

    base = os.getenv("PATH_TO_NG")
    if base:
        return str((Path(base).expanduser() / "ng.pt"))

    raise SystemExit(
        "Checkpoint path not provided.\n"
        "Provide it as an argument:\n"
        "  python scripts/serve.py <path_to_ng.pt>\n"
        "or set env:\n"
        "  NG_PT=<full path to ng.pt>\n"
        "  or PATH_TO_NG=<dir containing ng.pt>\n"
    )


def main() -> int:
    _load_dotenv_if_available()

    parser = argparse.ArgumentParser(description="Model inference server")
    # Make ckpt optional; fallback to env.
    parser.add_argument("ckpt", nargs="?", default=None, help="Path to checkpoint file (optional if NG_PT/PATH_TO_NG set)")
    parser.add_argument("--port", type=int, default=int(os.getenv("NG_PORT", "5555")), help="Port to serve on (default: 5555 or NG_PORT)")
    parser.add_argument("--old-layout", action="store_true", help="Use old layout")
    parser.add_argument("--cfg", type=float, default=float(os.getenv("NG_CFG", "1.0")), help="CFG scale (default: 1.0 or NG_CFG)")
    parser.add_argument("--ctx", type=int, default=int(os.getenv("NG_CTX", "1")), help="Context length (default: 1 or NG_CTX)")
    args = parser.parse_args()

    ckpt_path = _resolve_ckpt_path(args.ckpt)
    if not Path(ckpt_path).expanduser().exists():
        raise SystemExit(f"Checkpoint file not found: {ckpt_path}")

    session = InferenceSession.from_ckpt(
        ckpt_path,
        old_layout=args.old_layout,
        cfg_scale=args.cfg,
        context_length=args.ctx,
    )

    # Setup ZeroMQ
    context = zmq.Context.instance()
    socket = context.socket(zmq.REP)
    socket.bind(f"tcp://*:{args.port}")

    # Create poller
    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    print(f"\n{'='*60}")
    print(f"Server running on port {args.port}")
    print(f"Checkpoint: {ckpt_path}")
    print(f"Waiting for requests...")
    print(f"{'='*60}\n")

    try:
        while True:
            # Poll with timeout to allow Ctrl+C handling
            events = dict(poller.poll(timeout=100))
            if socket not in events or events[socket] != zmq.POLLIN:
                continue

            try:
                raw = socket.recv()
                request = pickle.loads(raw)
            except Exception as e:
                socket.send(pickle.dumps({"status": "error", "message": f"Bad request payload: {e}"}))
                continue

            rtype = request.get("type")
            try:
                if rtype == "reset":
                    session.reset()
                    response = {"status": "ok"}
                    print("Session reset")

                elif rtype == "info":
                    info = session.info()
                    response = {"status": "ok", "info": info}
                    print("Sent session info")

                elif rtype == "predict":
                    if "image" not in request:
                        response = {"status": "error", "message": "Missing field: image"}
                    else:
                        result = session.predict(request["image"])
                        response = {"status": "ok", "pred": result}

                else:
                    response = {"status": "error", "message": f"Unknown request type: {rtype}"}

            except Exception as e:
                # Catch inference-time errors so the server doesn't die
                response = {"status": "error", "message": f"Server error: {e}"}

            socket.send(pickle.dumps(response))

    except KeyboardInterrupt:
        print("\nShutting down server...")
        return 0
    finally:
        try:
            socket.close(linger=0)
        finally:
            context.term()


if __name__ == "__main__":
    raise SystemExit(main())
