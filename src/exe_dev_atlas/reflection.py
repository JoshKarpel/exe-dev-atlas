# exe.dev's reflection integration: how a VM learns its own name.
#
# Read once at startup rather than per request: it is a remote call, and the answer does not
# change over the life of a process.

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

import h11
from without_async import timeout
from without_http import Client
from without_http import request

# Reflection is an exe.dev default integration, and its root document carries the VM's name
# and emoji.
REFLECTION_URL: Final = "https://reflection.int.exe.xyz/"

REFLECTION_TIMEOUT: Final = timedelta(seconds=5)

# What VS Code's Remote-SSH opens. The VM name comes from reflection rather than the
# browser's location, because unlike every other link on this page it names a host to SSH
# to rather than one to fetch from: reached through a tunnel, `location.hostname` is
# `localhost`, which is the one machine this must not point at.
#
# The path names the folder to open, and there is no way to say "open none": a URL with no
# path resolves to `/` rather than to a folderless window, so omitting it trades the home
# directory for the whole filesystem. Only the CLI (`code --remote ssh-remote+<host>`) can
# open an empty remote window, and a clickable link cannot reach it.
# See microsoft/vscode#232345.
VSCODE_URL: Final = "vscode://vscode-remote/ssh-remote+{host}{directory}?windowId=_blank"
VM_SUFFIX: Final = ".exe.xyz"


@dataclass(frozen=True, slots=True)
class Vm:
    """How this machine identifies itself, per exe.dev's reflection integration."""

    name: str
    emoji: str


# What a VM that reflection could not describe is, so every way that lookup fails answers with
# one value rather than three constructions of the same blanks.
UNNAMED: Final = Vm(name="", emoji="")


async def read_vm(client: Client) -> Vm:
    """
    The VM's own name and emoji, or empty strings if reflection did not answer.

    Empty is the honest answer rather than a guess, and the page is written to treat it as
    one: it falls back to the hostname the browser used, keeps the built-in favicon, and
    offers no VS Code link.

    "Did not answer" has to cover every way this can fail, because it runs inside the
    lifespan and anything that escapes takes the whole startup with it: with `Restart=always`
    the service then restarts every five seconds rather than serving an unnamed page. A
    connection dropped mid-response arrives as h11's `RemoteProtocolError` rather than as an
    `OSError`, and a body that is not JSON as a `JSONDecodeError`, which is a `ValueError`.
    """
    try:
        async with timeout(REFLECTION_TIMEOUT), request(client, "GET", REFLECTION_URL) as (head, body):
            if head.status != 200:
                return UNNAMED
            published = json.loads(await body.read())
    except OSError, TimeoutError, ValueError, h11.RemoteProtocolError:
        return UNNAMED
    if not isinstance(published, dict):
        return UNNAMED
    return Vm(name=str(published.get("name") or ""), emoji=str(published.get("emoji") or ""))


def vscode_url(vm_name: str, directory: str) -> str:
    """A VS Code Remote-SSH workspace link, or "" when there is no VM to name."""
    if not vm_name:
        return ""
    return VSCODE_URL.format(host=f"{vm_name}{VM_SUFFIX}", directory=directory)
