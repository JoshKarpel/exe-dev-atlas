# Which VM this is, as the running process understands it, and everything rendered from that.
#
# This is the one place in the program, and it is a place on purpose: the refresh loop below
# writes here while the scan loop and the index handler read it, which is the whole mechanism
# by which a VM renamed under a running server reaches its own page. Everything the name and
# the emoji feed is rebuilt together, so no reader can catch a shell titled with one name
# beside a Remote-SSH link naming another.

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta

from without_asgi import Response
from without_http import Client

from exe_dev_atlas import reflection
from exe_dev_atlas.page import page_response
from exe_dev_atlas.reflection import REFLECTION_INTERVAL
from exe_dev_atlas.reflection import Reflection
from exe_dev_atlas.reflection import ReflectionFailed
from exe_dev_atlas.reflection import vscode_url

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Identity:
    """
    The VM reflection last described, and what the page says about it.

    `workspace` is the directory a VS Code link should open, or `None` where that link was
    turned off at install: an empty `vscode_url` is already how the page is told there is no
    link to offer, so the decision is made once here rather than carried any further in.
    """

    vm: Reflection
    workspace: str | None
    page: Response = field(init=False)
    vscode_url: str = field(init=False)

    def __post_init__(self) -> None:
        self.update(self.vm)

    def update(self, vm: Reflection) -> None:
        """Take `vm` as what this box is now, rebuilding everything that says so."""
        self.vm = vm
        self.page = page_response(vm)
        self.vscode_url = "" if self.workspace is None else vscode_url(vm.name, self.workspace)


async def refresh_forever(client: Client, identity: Identity, interval: timedelta = REFLECTION_INTERVAL) -> None:
    """
    Re-read reflection on a slow cadence, and write only what it actually answered.

    A lookup that fails leaves the identity exactly as it was. That is the point of reading it
    at startup as well: there is always a good answer to keep, and a stale name is a better
    description of this VM than a blank one. The first read has already happened by the time
    this starts, so the wait comes first.

    A failure is logged rather than raised, for the same reason the scan loop survives one:
    nothing watches this task, and `background_task` surfaces an exception only once the
    server shuts down, so a loop that died would freeze the heading for the life of the
    process with nothing anywhere to say why.
    """
    while True:
        await asyncio.sleep(interval.total_seconds())
        try:
            identity.update(await reflection.read_reflection(client))
        except ReflectionFailed as unanswered:
            logger.warning(f"Reflection did not answer and {identity.vm.name} stands: {unanswered!r}")
        except Exception:
            logger.exception("Reflection will not be read again, so a rename would go unnoticed")
            raise
