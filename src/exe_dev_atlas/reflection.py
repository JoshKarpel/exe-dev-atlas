# exe.dev's reflection integration: how a VM learns its own name and who owns it.
#
# Every answer here is read once at startup rather than per request. Each is a remote call,
# one of them decides an authorization question, and neither changes over the life of a
# process.

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from without_http import Client
from without_http import request

# Reflection is an exe.dev default integration. `/email` is the address the VM is owned by;
# the root document carries its name and emoji.
OWNER_URL: Final = "https://reflection.int.exe.xyz/email"
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


async def _published(client: Client, url: str) -> dict[str, object]:
    """
    One reflection document, or an empty one if it did not answer.

    Empty is the honest answer rather than a guess, and every caller here is written to
    treat it as one: the page falls back to the hostname the browser used, keeps the
    built-in favicon, offers no VS Code link, and discloses nothing.
    """
    try:
        async with asyncio.timeout(REFLECTION_TIMEOUT.total_seconds()):
            async with request(client, "GET", url) as (head, body):
                if head.status != 200:
                    return {}
                published = json.loads(await body.read())
    except OSError, TimeoutError, ValueError:
        return {}
    return published if isinstance(published, dict) else {}


async def read_owner_email(client: Client) -> str:
    """
    The address this VM is owned by, or "" if reflection did not answer.

    An empty answer denies rather than defaults: nothing is disclosed on the strength of a
    query that failed.
    """
    return str((await _published(client, OWNER_URL)).get("email") or "")


async def read_vm(client: Client) -> Vm:
    """The VM's own name and emoji, or empty strings if reflection did not answer."""
    published = await _published(client, REFLECTION_URL)
    return Vm(name=str(published.get("name") or ""), emoji=str(published.get("emoji") or ""))


def vscode_url(vm_name: str, directory: str) -> str:
    """A VS Code Remote-SSH workspace link, or "" when there is no VM to name."""
    if not vm_name:
        return ""
    return VSCODE_URL.format(host=f"{vm_name}{VM_SUFFIX}", directory=directory)
