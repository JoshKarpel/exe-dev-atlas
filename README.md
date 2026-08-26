# exe-dev-atlas

A front door for an [exe.dev](https://exe.dev) VM: what is listening, what it is, and what to
open.

exe.dev forwards ports 3000-9999 to `https://<vm>.exe.xyz:<port>/` with no configuration, so
a dev server started on one already has a URL. What it has no way to tell you is which ports
are live and what is on them. The atlas serves that list on the port the bare hostname points
at, so `https://<vm>.exe.xyz/` becomes a launcher for everything else on the box, alongside
the zellij sessions that have no port of their own.

```console
$ uv tool install exe-dev-atlas
$ exe-dev-atlas install
```

`install` writes a user systemd unit and starts it. `serve` runs the same server in the
foreground. Both take `--port` (default 8000, also read from `EXE_DEV_ATLAS_PORT`).

## What it shows

One row per listening port in the proxied range, pushed over SSE and updated within a second
of anything changing:

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

## What only the owner sees

The atlas is reachable by anyone the VM is shared with, so two things are withheld from
everyone but the address the VM is owned by, as exe.dev's proxy reports it:

- **zellij session names**, which are often a project or a client name.
- **the VS Code Remote-SSH link**, which only works for someone with SSH access anyway.

Ownership is decided once at startup from exe.dev's reflection integration, and the
comparison fails closed: a reflection lookup that did not answer and an unauthenticated caller
both produce an empty address, and an empty address matches nobody. A box whose lookup failed
therefore serves the public view to everyone until it is restarted.

A zellij web server is the one port not linked directly. Arriving there without a session
named in the path does not land on a picker, it creates a new session, so a link to its root
would litter the box with an empty session per visit. Its existing sessions are listed
individually instead, which is both the useful destination and the harmless one.

## The systemd unit

`install` names the interpreter that ran it. `exe-dev-atlas install` is invoked *by* the
installed CLI, so `sys.executable` is already an absolute path to an interpreter holding this
package and its dependencies, and nothing has to be looked up on `PATH` or derived from a
login shell:

```ini
ExecStart=/home/you/.local/share/uv/tools/exe-dev-atlas/bin/python -m exe_dev_atlas serve --port 8000
```

This tool does not fetch, build, or manage a Python environment. Whoever installed the package
chose the version; `install` only points systemd at it. To upgrade, upgrade the package and
run `install` again: an upgrade in place renders an identical unit, so the restart is what
puts the new code in front of anything, and it happens whether the unit changed or not.

The unit carries only a standard system `PATH`. `ss` is the one thing looked up on it; the
zellij binary a session lookup runs is read from `/proc/<pid>/exe`, so it is the exact binary
that is serving rather than whatever a lookup would find, and it needs no `PATH` entry.

`WantedBy=default.target` starts the unit when the *user manager* starts, which without
`loginctl enable-linger <user>` is at first login rather than at boot. `install` checks this
and says so if it is off, because the failure is otherwise invisible: the unit is enabled, the
file is correct, and nothing is running.

## Requirements

Linux, Python 3.14, and `iproute2` for `ss`. Every listener fact comes from `ss` and `/proc`,
so this is not portable off Linux, and the proxy's port range, its authentication header, and
the reflection integration are all assumed to be exe.dev's.

## Development

```console
$ just setup
$ just test
```

`just --list` shows the rest. Built on [`without`](https://without.help): `without-http`
serves, `without-web` routes, `without-html` renders the shell, `without-async` supervises the
scan task, and `without-asgi` both frames the event stream and serves the stylesheet and
script out of an inventory walked once at startup.
