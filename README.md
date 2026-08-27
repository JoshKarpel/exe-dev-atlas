# exe-dev-atlas

A port explorer for an [exe.dev](https://exe.dev) VM:
which ports are bound, what process bound them, and links to access them.

exe.dev [forwards ports 3000-9999](https://exe.dev/docs/proxy#additional-ports)
to `https://<vm>.exe.xyz:<port>/` with no configuration, so a dev server started on one already has a URL. What it has no way to tell you is which ports are live and what is on them.
The atlas serves that list [on the port the bare hostname points at](https://exe.dev/docs/proxy#configuring-which-port-to-proxy) (or whatever other port you choose),
so `https://<vm>.exe.xyz/` becomes a launcher for everything else on the box.

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

### Run this only on a VM you would hand somebody a shell on

Every row carries a process's full command line, its working directory, its user, and its
pid, and that is the *public* half of what this serves. Command lines routinely carry
secrets: a `--backend-store-uri postgresql://user:hunter2@db/app`, an `--api-key`, a token
in an argument. `/proc/<pid>/cmdline` is world-readable, so the listing covers other users'
processes on the box too, whatever this daemon can and cannot read of them.

So this page is only as private as the VM it runs on, and exe.dev's
[sharing](https://exe.dev/docs/sharing) controls are the only thing between it and a reader:

- **Never make a VM running the atlas public.** `share set-public <vm>` drops the login
  requirement on the [proxied](https://exe.dev/docs/proxy) port, and an unauthenticated
  caller sends no `x-exedev-email` header, so anybody who finds the hostname gets the page.
- **Read a Web share as a share of every command line on the box.** `share add <vm> <email>`
  grants access to the VM's HTTPS proxy, and this page is on it. Withholding the zellij
  session names and the VS Code link from a non-owner is one degree less detail on the same
  page, not confidentiality.
- **The port itself is defended by nothing.** The header the owner check reads is added by
  exe.dev's proxy. A caller that reaches the port without making that hop, an SSH tunnel or
  another user on the box, sends whatever address it likes and is served the owner's view.

`share show <vm>` says who has access today.

### What it shows

One row per listening process in the proxied port range, pushed over SSE and updated within a
second of anything changing. One port carries two rows where two processes hold it between
them, one on loopback and one on a LAN address, since they are two services:

- The port, linked, unless it is this page's own or it did not answer HTTP.
- Whatever the port called itself: the `<title>` of the page it served, falling back to the
  process name.
- The process behind it, its working directory, its owner, and how long it has been up.
- `1`-`9` open the first nine links from the keyboard.

Listeners are found by polling, because the kernel offers no alternative: netlink's
`inet_diag` answers a query rather than announcing a new socket, and `/proc/net/tcp` cannot be
watched. A scan costs a few milliseconds, so it runs once a second and pushes only when the
result differs from the last one.

Links are built from the browser's own location rather than from the VM name, so they stay
correct whether the page was reached through the exe.dev proxy or an SSH tunnel to the same
port.

### What only the owner sees

The atlas is reachable by anyone the VM is shared with, so three things are withheld from
everyone but the address the VM is owned by, as exe.dev's proxy reports it. This is a
smaller distinction than it sounds, and the section above says what it does not cover:

- **zellij session names**, which are often a project or a client name.
- **the link that starts a new zellij session**, which is the one thing on the page that acts
  on the box rather than pointing at something already running on it.
- **the VS Code Remote-SSH link**, which only works for someone with SSH access anyway.

Ownership is decided once at startup from exe.dev's reflection integration, and the
comparison fails closed: a reflection lookup that did not answer and an unauthenticated caller
both produce an empty address, and an empty address matches nobody. A box whose lookup failed
therefore serves the public view to everyone until it is restarted.

A zellij web server is the one port whose row is not itself a link. Arriving there without a
session named in the path does not land on a picker, it creates a new session, so a row that
opened its root would litter the box with an empty session per visit. The owner gets its
existing sessions listed individually beneath the row instead, followed by a `+ new session`
link that asks for that same behaviour on purpose. The row's own anchor stays inert and the
new-session link takes no hotkey digit, so the one link on the page that creates something is
reached only by clicking it.

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

`--systemd-unit-suffix` installs under a name of its own, so a second atlas sits beside the
first instead of converging onto it:

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
