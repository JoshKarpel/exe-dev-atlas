# exe-dev-atlas

A port explorer for an [exe.dev](https://exe.dev) VM:
which ports are bound, what process bound them, and links to access them,
as a web page served from the VM itself.

exe.dev automaticaly [forwards ports 3000-9999](https://exe.dev/docs/proxy#additional-ports)
to `https://<vm>.exe.xyz:<port>/`, so a server started on one already has a URL.
`exe-dev-atlas` serves a lightweight page
[on the port the bare hostname points at](https://exe.dev/docs/proxy#configuring-which-port-to-proxy) (or whatever other port you choose)
with links to and information about programs that are listening on those ports,
so that `https://<vm>.exe.xyz/` becomes a launcher for everything else on the box.

This can be useful when ["multiplexing"](https://en.wikipedia.org/wiki/Multiplexing) multiple servers on the same VM.
A typical use case might be development work where you want to simultaneously expose
[terminal sessions](https://zellij.dev/documentation/web-client.html),
[editor sessions](https://code.visualstudio.com/docs/remote/vscode-server),
[a docs dev server](https://www.mkdocs.org/user-guide/cli/#mkdocs-serve),
[your service](https://fastapi.tiangolo.com/deployment/manually/),
and [your agent](https://exe.dev/docs/shelley/intro) all at the same time,
and you want to have a convenient web UI to click into them
instead of needing to remember and type the ports by hand.

<!-- Absolute rather than repository-relative, because PyPI renders this file too: its renderer
     leaves a relative `src` alone, so the browser resolves it against pypi.org and gets a 404,
     and it strips the `<source>`, so the PyPI page shows the light shot either way. -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/JoshKarpel/exe-dev-atlas/main/docs/screenshot-dark.png">
  <img alt="One row per listening process, each carrying the port, the title the port served,
  the working directory, the command line, and the uptime, under a VS Code link"
  src="https://raw.githubusercontent.com/JoshKarpel/exe-dev-atlas/main/docs/screenshot-light.png">
</picture>

## Install

```console
$ uv tool install exe-dev-atlas
$ exe-dev-atlas install
```

exeuntu, the default exe.dev image, ships with [uv](https://docs.astral.sh/uv/), so there is
nothing to install before that first line.

`install` writes a user systemd unit and starts it. `serve` runs the same server in the
foreground. Both take `--port` (default 8000, also read from `EXE_DEV_ATLAS_PORT`).

## Security

All the atlas provides is discoverability. It grants nothing, gates nothing, and changes no
access control on the VM: exe.dev's proxy decides who reaches the box, every port in the
proxied range was reachable by those people before this was installed, and the atlas
authenticates nobody, so it serves the same page to everyone who gets that far.

What changes is how much work it takes to find things. A reader who would have had to guess a
port number is handed the list, and the list is detailed: every row carries a process's full
command line, its working directory, its user, and its pid.

So the VM's [sharing](https://exe.dev/docs/sharing) settings are the whole boundary, exactly
as they were before, and the atlas is a reason to read them carefully:

- **Never make a VM running the atlas public.** `share set-public <vm>` drops the login
  requirement on the [proxied](https://exe.dev/docs/proxy) port, so anybody who finds the
  hostname gets the page and everything on it.
- **Read a Web share as full access to every web server on the box.**
  `share add <vm> <email>` grants access to the VM's HTTPS proxy, with all that implies about
  what is listening on it: a dev server, a notebook, a Zellij web server that hands out a
  terminal.
- **The port itself is defended by nothing.** exe.dev's proxy is the only thing
  authenticating anyone. A caller that reaches the port without making that hop, an SSH
  tunnel or another user on the box, is served the page like everybody else.

`share show <vm>` says who has access today.

## Beyond Port Exploration

`exe-dev-atlas` does a few things beyond generic port exploration.

### VS Code

`exe-dev-atlas` shows a link under the header that opens your *local* VS Code in
[remote SSH mode](https://code.visualstudio.com/docs/remote/ssh) targetting the VM.

This can be convenient, but you might want to
[run VS Code Server](https://code.visualstudio.com/docs/remote/vscode-server)
on your VM instead (as a `systemd` service, of course!).

If you don't find this link helpful,
you can disable it by running `exe-dev-atlas install --no-vs-code-link`.
Both commands take `--vs-code-link/--no-vs-code-link`,
and `install` records whichever you asked for in the unit,
so you must pass it each time you call `install`.

### Zellij

`exe-dev-atlas` has specialized support for [Zellij's](https://zellij.dev/documentation/introduction.html)
[`web` server](https://zellij.dev/documentation/web-client.html).
When `exe-dev-atlas` detects that a process is running `zellij web`, it run `zellij list-sessions` to discover which [sessions](https://zellij.dev/documentation/commands.html#attach-session-name)
are already active and produces direct links to them as well, alongside a link that starts a
new one.

## Installation

### Requirements

`exe-dev-atlas` is, unsurprisingly, intended to run on an [exe.dev VM](https://exe.dev/docs/what-is-exe).
We assume the basic structure of their [`exeuntu` image](https://github.com/boldsoftware/exeuntu),
such as being Linux,
being able to configure [`systemd`](https://en.wikipedia.org/wiki/Systemd),
`uv` pre-installed,
the port proxy structure and default bare-hostname port,
[authentication](https://exe.dev/docs/login-with-exe)
and the [reflection](https://exe.dev/docs/integrations-reflection) mechanism, etc.

### The `systemd` unit

`exe-dev-atlas` installs itself as a `systemd` unit that keeps it running through restarts, crashes, etc.

`exe-dev-atlas install` is invoked *by* the installed CLI,
so `sys.executable` is already an absolute path to an interpreter holding this
package and its dependencies, and nothing has to be looked up on `PATH` or derived from a
login shell:

```ini
ExecStart=/home/you/.local/share/uv/tools/exe-dev-atlas/bin/python -m exe_dev_atlas serve --port 8000 --vs-code-link
```

This tool does not fetch, build, or manage a Python environment. Whoever installed the package
chose the version; `install` only points systemd at it. To upgrade, upgrade the package and
run `install` again: an upgrade in place renders an identical unit, so the restart is what
deploys the changes, and it happens whether the unit changed or not.

### More than one atlas on a box

When working on `exe-dev-atlas` itself, it might be convenient to run it twice on the same VM.
Therefore, the `--systemd-unit-suffix <suffix>` options installs the systemd unit under a
suffixed name, so that a second atlas sits beside the first instead of overwriting it:

```console
$ exe-dev-atlas install --systemd-unit-suffix dev --port 8001
installed /home/you/.config/systemd/user/exe-dev-atlas-dev.service
restarted exe-dev-atlas-dev, serving on port 8001 from /home/you/src/exe-dev-atlas/.venv/bin/python
```

Every install reaches exactly the unit it rendered, so the one above leaves `exe-dev-atlas`
running whatever it was already running. Give each its own `--port`: nothing stops two units
from being told to bind the same one, and the loser restarts every five seconds. The suffix
may hold letters, digits, hyphens, and underscores.

This is what a checkout wants, and what `just install` in this repository does: working on the
atlas shouldn't take down the one serving the VM's front door.

## Development

We use `mise` to manage tool installs and `just` to manage recipes.

```console
$ mise install
$ just setup
$ just test
```

`just --list` shows the rest.
