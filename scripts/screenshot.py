#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["playwright>=1.62"]
#
# [tool.uv]
# exclude-newer = "7 days"
# ///
# The README's screenshots, captured from whatever this machine is running.
#
# A script rather than a fixture, because the picture worth putting in a README is of a real
# box: real ports, probed and rendered by the same code a reader will install. Nothing here
# fabricates a row.
#
# Its dependency lives in the header above rather than in the project's dev group, so a
# `uv sync` for the test suite does not pull 140 MB of browser driver. Two environments follow
# from that, this one and the project's, which is why the atlas is a subprocess and not an
# import.
#
# Both colour schemes are captured, because the stylesheet answers to `prefers-color-scheme`
# and GitHub's `<picture>` element serves whichever one the reader is in.
#
# The page advertises the host the browser reached it by, which here is loopback rather than
# the `<vm>.exe.xyz` a reader would use. Pointing the browser at the VM's own name is not
# available: `exe.xyz` is in Chromium's HSTS preload list, so the browser upgrades the request
# to HTTPS and nothing on this side is speaking it.

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from playwright.sync_api import Browser
from playwright.sync_api import sync_playwright

REPO: Final = Path(__file__).resolve().parent.parent
SHOTS: Final = REPO / "docs"

# Inside the proxied range (3000-9999) and unlikely to be taken, so the atlas lists its own row
# and badges it `this page`, which is one of the things the shot is meant to show. Not the port
# CLAUDE.md hands an agent for `just serve`, so a foreground atlas and a capture can coexist.
DEFAULT_PORT: Final = 8765

# The address exe.dev's proxy would report for the VM's owner, and the header it would report it
# in. Sent so the shot carries the owner's view: zellij session names and the VS Code link.
OWNER_URL: Final = "https://reflection.int.exe.xyz/email"
CALLER_EMAIL_HEADER: Final = "x-exedev-email"

# `main` is capped at 62rem, so this leaves a margin either side rather than cropping to the
# content. Doubled on capture, since a README image is displayed at half its pixel width.
VIEWPORT: Final = {"width": 1120, "height": 900}
SCALE: Final = 2

STARTUP_TIMEOUT: Final = 60.0
RENDER_TIMEOUT: Final = 60_000

# Waited for rather than slept through, so the shot is of a settled page however long this box
# takes to get there. `atlas.js` writes `probing…` into a row's title for as long as its probe
# is outstanding, so the last one to change is the last one to be answered.
SETTLED: Final = """() => {
  const titles = [...document.querySelectorAll('#ports .title')];
  return document.getElementById('state').textContent === 'live'
    && titles.length > 0
    && titles.every((title) => title.textContent !== 'probing…');
}"""


def install_browser() -> None:
    """
    Fetch the headless browser if this machine does not have it yet.

    Unconditional because it is already the idempotent form: with the build present it exits
    silently in well under a second, which is cheaper than any check written here.
    """
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)


def owner_email() -> str:
    """
    The address this VM is owned by, per exe.dev's reflection integration, or "" off exe.dev.

    Empty is not a failure. The atlas fails closed on an empty address, so the shot is then of
    the public view, which is what a reader off exe.dev would be served anyway.
    """
    try:
        with urllib.request.urlopen(OWNER_URL, timeout=5) as answer:
            published = json.loads(answer.read())
    except OSError, ValueError:
        return ""
    return str(published.get("email") or "") if isinstance(published, dict) else ""


@contextmanager
def atlas_serving(port: int) -> Iterator[str]:
    """
    Serve this repository's atlas on `port` for as long as the block runs.

    `VIRTUAL_ENV` is dropped because this script has an environment of its own, and the inner
    `uv run` would otherwise warn that the one it is activating is not the one it inherited.
    """
    environment = os.environ | {"VIRTUAL_ENV": ""}
    server = subprocess.Popen(
        ["uv", "run", "--project", str(REPO), "exe-dev-atlas", "serve", "--port", str(port)],
        env=environment,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        wait_until_answering(url, server)
        yield url
    finally:
        server.terminate()
        server.wait(timeout=10)


def wait_until_answering(url: str, server: subprocess.Popen[bytes]) -> None:
    """Block until the atlas answers, or say why rather than screenshotting a blank page."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SystemExit(f"The atlas exited with {server.returncode} before it served anything.")
        try:
            with urllib.request.urlopen(url, timeout=1) as answer:
                if answer.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise SystemExit(f"{url} did not answer within {STARTUP_TIMEOUT:.0f}s.")


def capture(browser: Browser, url: str, scheme: str, into: Path, email: str) -> None:
    """One full-page shot in one colour scheme, taken once every row has been probed."""
    context = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=SCALE,
        color_scheme=scheme,
        extra_http_headers={CALLER_EMAIL_HEADER: email} if email else {},
    )
    try:
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function(SETTLED, timeout=RENDER_TIMEOUT)
        page.screenshot(path=into, full_page=True)
    finally:
        context.close()
    print(f"{scheme}: {into.relative_to(REPO)}")


@contextmanager
def target(url: str, port: int) -> Iterator[str]:
    """The atlas to shoot: one already running where the caller said, or one served here."""
    if url:
        yield url
        return
    with atlas_serving(port) as served:
        yield served


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture the README's screenshots from this machine.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="the port to serve the atlas on")
    parser.add_argument("--url", default="", help="shoot an atlas already running here instead of serving one")
    parser.add_argument("--public", action="store_true", help="shoot the public view rather than the owner's")
    arguments = parser.parse_args()

    SHOTS.mkdir(parents=True, exist_ok=True)
    install_browser()

    email = "" if arguments.public else owner_email()
    if not email:
        print("These are of the public view: no zellij session names, no VS Code link.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            with target(arguments.url, arguments.port) as url:
                for scheme in ("light", "dark"):
                    capture(browser, url, scheme, SHOTS / f"screenshot-{scheme}.png", email)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
