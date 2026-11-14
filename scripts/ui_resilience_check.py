"""Break the upload on purpose, in a real browser, and check the UI recovers (developer tool).

    python scripts/ui_resilience_check.py

Scenario 1: one chunk request is dropped by the network. The page must retry, resume, and the
run must still finish with every plate.
Scenario 2: the network dies for good after a few chunks. The page must say so, tell the
server, and the very next upload must be accepted (the server must not stay "busy").
Scenario 3: the tab is reloaded mid-run. The next upload must be accepted straight away.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / "REC20261007-174724-7378.mp4"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def text(page, selector: str) -> str:
    loc = page.locator(selector)
    return loc.inner_text() if loc.count() and loc.is_visible() else ""


def main() -> int:
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "ui", "--no-browser", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/"
    failures: list[str] = []
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)

            # ---- scenario 1: one dropped chunk, page retries and resumes
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            seen = {"n": 0}

            def drop_once(route):
                seen["n"] += 1
                if seen["n"] == 6:
                    route.abort("connectionreset")
                else:
                    route.continue_()

            page.route("**/data?offset=*", drop_once)
            page.goto(url)
            page.wait_for_selector("#sample-chips .sample")
            page.set_input_files("#file", str(VIDEO))
            page.wait_for_selector("#status[data-state='done']", timeout=300000)
            plates = page.locator("#plate-list .card").count()
            requests = seen["n"]
            print(f"scenario 1: one chunk dropped ({requests} requests), plates={plates}")
            if plates != 5:
                failures.append(f"scenario 1 expected 5 plates, saw {plates}")
            page.close()

            # ---- scenario 2: the network dies for good
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            count = {"n": 0}

            def die_after_three(route):
                count["n"] += 1
                if count["n"] > 3:
                    route.abort("connectionreset")
                else:
                    route.continue_()

            page.route("**/data?offset=*", die_after_three)
            page.goto(url)
            page.wait_for_selector("#sample-chips .sample")
            page.set_input_files("#file", str(VIDEO))
            page.wait_for_selector("#status[data-state='error']", timeout=60000)
            banner = text(page, "#banner")
            print("scenario 2: page says:", repr(banner))
            if "Upload failed" not in banner:
                failures.append("scenario 2: no clear upload-failed message")
            page.unroute("**/data?offset=*")
            page.click("#again")
            time.sleep(1.0)  # the page has told the server to stop; the slot must be free
            page.click("#sample-chips .sample >> nth=1")
            page.wait_for_selector("#status[data-state='analyzing']", timeout=20000)
            print("scenario 2: next run was accepted straight away")
            page.click("#cancel")
            page.wait_for_selector("#status[data-state='cancelled']", timeout=30000)
            page.close()

            # ---- scenario 3: reload the tab mid-run
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(url)
            page.wait_for_selector("#sample-chips .sample")
            page.click("#sample-chips .sample >> nth=1")
            page.wait_for_selector("#plate-list .card", timeout=60000)
            page.reload()
            page.wait_for_selector("#sample-chips .sample")
            time.sleep(1.5)
            page.click("#sample-chips .sample >> nth=1")
            page.wait_for_selector("#status[data-state='analyzing']", timeout=20000)
            banner = text(page, "#banner")
            print("scenario 3: after a reload the next run was accepted; banner:", repr(banner))
            if "still being analysed" in banner:
                failures.append("scenario 3: server stayed busy after the tab was reloaded")
            page.click("#cancel")
            browser.close()
        if failures:
            print("FAILED:", failures)
            return 1
        print("OK: the UI recovers from all three failures")
        return 0
    finally:
        server.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
