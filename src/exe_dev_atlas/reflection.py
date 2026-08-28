# exe.dev's reflection integration: how a VM learns its own name.
#
# The atlas is an index *of a named VM*. The tab, the heading, and the Remote-SSH link are all
# this one answer, so the lookup is a requirement rather than a decoration: it is read before
# the server binds anything, and a failure there is a failed startup. What that buys is a
# process that either knows which box it is describing or is not running.
#
# It is read again on a slow cadence afterwards, since the answer changes only when somebody
# renames the VM.

from __future__ import annotations

from datetime import timedelta
from typing import Final

import h11
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError
from without_async import timeout
from without_http import Client
from without_http import request

# Reflection is an exe.dev default integration, and its root document carries the VM's name
# and emoji.
REFLECTION_URL: Final = "https://reflection.int.exe.xyz/"

REFLECTION_TIMEOUT: Final = timedelta(seconds=5)

# How long an answer stands before it is asked for again. Slow because the only thing that
# changes it is somebody renaming the VM, and the cost of noticing that a few minutes late is
# a heading that is briefly out of date.
REFLECTION_INTERVAL: Final = timedelta(minutes=5)

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


class ReflectionFailed(RuntimeError):
    """
    Reflection did not answer, or answered without naming the VM.

    Carries a message a reader can act on rather than a type to match on, because that message
    is the whole of what a failed startup gets to say: an ASGI lifespan reports a startup
    failure as a string, so whatever is not in here is not in the journal either.
    """


class Reflection(BaseModel):
    """
    How this machine identifies itself: the reflection document, as much of it as is read.

    The name is what makes the document worth reading and is required as such, so a `name`
    here is never empty: an answer without one describes no VM, and the parse fails rather
    than handing blanks to a page that would then be unable to say which box it is. The emoji
    is decoration and may be absent, so it defaults instead.

    Everything else in the document (the owner's email, the integrations, the tags) is ignored
    by omission, which is also what keeps a field added on exe.dev's side from failing a parse
    that never wanted it.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    emoji: str = ""


def parse_reflection(document: bytes) -> Reflection:
    """The VM the reflection document describes, or a failure naming what was wrong with it."""
    try:
        return Reflection.model_validate_json(document)
    except ValidationError as malformed:
        raise ReflectionFailed(f"{REFLECTION_URL} answered with no VM in it: {malformed}") from malformed


async def read_reflection(client: Client) -> Reflection:
    """
    Ask reflection what this VM is called, or fail saying why it could not be asked.

    Every way this can go wrong comes back as one exception, because every one of them means
    the same thing to the caller: this process does not know which VM it is on. A connection
    dropped mid-response arrives as h11's `RemoteProtocolError` rather than as an `OSError`,
    and a body that is not the document we asked for is the parse's business rather than this
    function's.
    """
    try:
        async with timeout(REFLECTION_TIMEOUT), request(client, "GET", REFLECTION_URL) as (head, body):
            if head.status != 200:
                raise ReflectionFailed(f"{REFLECTION_URL} answered {head.status} rather than 200")
            document = await body.read()
    except (OSError, TimeoutError, h11.RemoteProtocolError) as unreachable:
        raise ReflectionFailed(f"{REFLECTION_URL} could not be read: {unreachable!r}") from unreachable
    return parse_reflection(document)


def vscode_url(vm_name: str, directory: str) -> str:
    """A VS Code Remote-SSH workspace link, naming the VM as the host to open `directory` on."""
    return VSCODE_URL.format(host=f"{vm_name}{VM_SUFFIX}", directory=directory)
