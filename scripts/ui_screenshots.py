"""Drive the live UI in a real browser and save screenshots (developer tool).

    python scripts/ui_screenshots.py [--out docs/ui] [--sample REC...mp4]

Needs `pip install playwright` and a system Chrome or Edge (no browser download). Starts the
server itself on a free port, runs one sample clip end to end, and fails loudly if the page
does not show plates, latency numbers and a finished state.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "ui")
    parser.add_argument("--sample", default="REC20261007-174724-7378.mp4")
    parser.add_argument("--channel", default="chrome", help="chrome or msedge")
    parser.add_argument(
        "--upload", action="store_true", help="upload the file instead of using the sample button"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "ui", "--no-browser", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=args.channel, headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="dark")
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(url)
            page.wait_for_selector("#sample-chips .sample")
            page.screenshot(path=args.out / "1-idle-dark.png")

            if args.upload:
                page.set_input_files("#file", str(ROOT / args.sample))
                page.wait_for_selector("#stage[data-mode='live']", timeout=60000)
                print("upload lane when analysis began:", page.locator("#up-val").inner_text())
            else:
                page.click(f"#sample-chips .sample:has-text('{args.sample[:-4]}')")
            page.wait_for_selector("#plate-list .card", timeout=60000)
            if args.upload:
                print("upload lane at first plate:", page.locator("#up-val").inner_text())
            page.wait_for_timeout(1500)
            page.screenshot(path=args.out / "2-analysing-dark.png")

            page.click("#plate-list .card >> nth=0")  # open the evidence strip
            page.wait_for_selector("#status[data-state='done']", timeout=240000)
            page.wait_for_timeout(600)
            page.screenshot(path=args.out / "3-finished-dark.png")

            cards = page.locator("#plate-list .card").count()
            plates = page.locator("#plate-list .card .plate").all_inner_texts()
            latency = page.locator("#lat li[data-key='frame'] .val").inner_text()
            summary = page.locator("#summary").inner_text()
            print("plates on page:", [p.replace("\n", "") for p in plates])
            print("whole-frame latency:", latency, "| summary:", summary.replace("\n", " "))

            page.click("#theme")
            page.wait_for_timeout(300)
            page.screenshot(path=args.out / "4-finished-light.png")

            phone = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
            phone.goto(url)
            phone.wait_for_selector("#sample-chips .sample")
            phone.screenshot(path=args.out / "5-phone-idle.png", full_page=True)
            browser.close()

        assert cards >= 3, f"expected at least 3 plate cards, saw {cards}"
        assert latency.strip() not in ("", "-"), "latency numbers never appeared"
        assert not errors, f"browser errors: {errors[:3]}"
        print("OK, screenshots in", args.out)
        return 0
    finally:
        server.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
