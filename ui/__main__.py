"""python -m ui  : start the live UI on http://127.0.0.1:8765"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import webbrowser
from pathlib import Path

from anpr.runtime import apply_environment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ui", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 only on a trusted network")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument(
        "--samples", type=Path, help="folder of videos shown as samples (default: project folder)"
    )
    args = parser.parse_args(argv)

    apply_environment(1)  # before numpy / onnxruntime load
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)

    from ui.server import serve

    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="plate-stream-") as work_dir:
        server = serve(args.host, args.port, root, Path(work_dir), args.samples)
        shown = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        url = f"http://{shown}:{args.port}/"
        print(f"Plate Stream is running at {url}  (Ctrl+C to stop)")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping.")
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
