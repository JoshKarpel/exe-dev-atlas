# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```console
$ just setup            # uv sync + install pre-commit as a git hook
$ just test             # mypy, then pytest
$ just test tests/test_scan.py::test_name   # extra args go straight to pytest
$ just check            # pre-commit over all files, then mypy
$ just serve --port 8123  # foreground, on a non-default port
$ just logs             # journalctl --user -u exe-dev-atlas -f
```

`pytest` runs under `xdist` (`-n auto`), `pytest-randomly`, and a 10-second per-test
timeout, all from `addopts`. A test that needs longer raises it with
`@pytest.mark.timeout(...)` rather than changing the global.

## Dependencies

Built on [`without`](https://without.help), a workspace of small sans-IO ASGI/HTTP/HTML
libraries. Read the installed packages in `.venv` rather than guessing at an API from
memory.

`uv` resolution has a 7-day cooldown (`exclude-newer`), with the `without-*` packages
exempted in `[tool.uv.exclude-newer-package]`. Adding a `without` package means adding it
to that exemption list too, or the whole graph gets held back to a release predating it.

`h11` is a direct dependency although only `without-http` calls it: an HTTP/1.1 parse failure
reaches a caller as `h11.RemoteProtocolError`, which is the ordinary answer from a listener
that is not a web server, so `probes` and `reflection` both catch it by name.

Python 3.14 only. The code uses unparenthesized multi-exception `except OSError, ValueError:`
(PEP 758) in several places; that is valid 3.14 syntax, not the Python 2 bind form.

## Architecture

### One scan loop, two payloads, one authorization decision

`app.build_app` is the composition root. Its lifespan builds an `Atlas` (a `Broadcast`, the
owner's email, the pre-rendered page `Response`) once, then binds `scan.scan_forever` to the
server's lifetime with `background_task`. Handlers see nothing but the `Atlas`.

`scan_once` reads the machine and publishes: `ss` for listeners, `/proc` for their processes,
`zellij list-sessions` for session servers, gathered rather than awaited in turn so one hung
session server does not hold every row behind its timeout. It serializes **two** JSON
payloads, a public one and an owner one carrying zellij session names and the VS Code link,
and hands both to `Broadcast.publish`, which only bumps its version when the pair differs
from the last. Both are built every scan even with no owner connected, because the diff is
against the pair.

`scan_forever` is the cadence around it, and it must **not** die on a bad scan: nothing
watches this task, so a page holding the last payload keeps its heartbeated connection and
reads "live" over a listing that stopped moving. `ss` exiting non-zero is logged and the next
scan retries; anything else is logged on the way out, since `background_task` surfaces a
task's exception only when the server shuts down. `main.serve` configures logging to stderr,
which under the unit is the journal (`just logs`).

Polling is deliberate, not a stopgap: the kernel offers no way to watch for a new listening
socket.

A row is one *listening process*, not one port: `parse_listeners` groups on `(port, pid)`, so
one pid bound on IPv4 and IPv6 is one row while two processes sharing a port number are two.
Anything keying rows by port alone (`atlas.js` looks its elements up by `port/pid`) collides
the moment that happens.

Which payload a connection gets is decided in `app.events`, the only place holding the
caller's headers. `app.is_owner` compares exe.dev's `x-exedev-email` header against the
owner address read from reflection at startup, and **fails closed**: both sides must be
non-empty, so a failed reflection lookup or an unauthenticated caller yields `""`, which
matches nobody. A box whose lookup failed serves the public view until restarted. The *last*
header value wins, because a proxy that appends leaves the client's own value first.

Only the proxy authenticates anyone, so a caller that reaches the port without that hop is
believed. The README says so where somebody deciding how to share a VM will read it, and the
public payload carries every command line on the box, which is the real reason it matters.

### What must not cross the wire

`listeners.Process` carries `executable` (from `/proc/<pid>/exe`), which `zellij.read_sessions`
needs to invoke the exact binary that is serving. `scan.build_row` deliberately drops it: a
`Row` is serialized straight to every connected browser. Keep that split when adding fields.

### Probing is off the scan loop

`probes.Probes` fires probes as tasks and holds results in a dict keyed by `(port, pid)`, so
a restarted process is re-probed and a port that accepts a connection then says nothing does
not stall every other row behind its timeout. A finished probe does not push; the next scan
carries it.

Every outcome must come back as a `Probe` value. A probe that raises is never recorded, so
`_is_due` sees no result and re-probes the same port on every scan forever. `read_beginning`
stops the body read at `PROBE_MAX_BYTES` rather than reading it all and slicing, which is
both the bound on what a hostile listener can make this hold and what keeps a `/` that
streams from costing the whole timeout.

### Install is convergence, not packaging

`install.py` renders a user systemd unit naming `sys.executable`, unresolved (resolving a
venv's `bin/python` symlink yields a base interpreter that cannot import the package). It
never fetches or builds an environment. In `converge`, the `daemon-reload` is conditional on
the unit text changing but the `restart` is **unconditional**: an upgrade in place renders
identical text, so the restart is the only thing that puts new code in front of anything.

`systemctl` is injected as a `Systemctl` callable so tests drive convergence without a
service manager.

### Functional core

`parse_listeners`, `ticks_from_stat`, `build_row`, `is_owner`, `unit_text`, `is_zellij_web`,
`format_probe_title`, and `page.shell` are pure and tested directly. The I/O shell around
them is thin: `processes.run` returns a `Ran` value (a timeout is an outcome, not an
exception; `.checked()` is the loud version), and reflection failures return empty values
that every caller is written to treat as an honest "no answer".

### Frontend

`static/atlas.js` renders everything from the SSE payload and builds links from
`location.hostname`, so they stay correct through the exe.dev proxy or an SSH tunnel alike.
The one exception is the VS Code Remote-SSH link, built server-side from the reflection VM
name because it names a host to SSH to rather than one to fetch from.

`page.py` server-renders only a constant shell (element ids the script looks up, two asset
links). `app.build_router` serves `static/` from an `inventory` walked once at startup, so
nothing may write into that directory while the process runs.

The payload is JSON rendered by hand rather than HTML fragments swapped by htmx, which is the
obvious thing to reach for over a stream like this one. htmx is a client for SSE, not an
alternative to it (`htmx-ext-sse` gives `sse-connect` and `sse-swap`), so the trade is where
rendering lives, and three things here price it:

- **Links are host-relative.** `href`s built server-side need the host each client used,
  which makes the payload per-connection and gives up what `Broadcast` is built around:
  serialize once per scan, share across every connection, diff the pair to decide there is
  news. Keeping a script to rewrite `href`s after each swap means running both.
- **A 1 Hz `innerHTML` swap is destructive.** `atlas.js` updates individual fields in place so
  the cadence does not take text selection, focus, and the `flash` animation with it.
  Swapping the whole list needs per-row out-of-band swaps keyed on `port/pid` plus idiomorph
  to morph rather than replace, which is a third asset to carry and `keyFor` relocated.
- **Nothing for htmx to attach to.** The page has no form, no button, and no navigation that
  reaches the server; its one interaction is an anchor. htmx's reason to exist, hypermedia
  controls on any element, would go unused, leaving it a DOM-patching library over a stream.

Polling (`hx-trigger="every 1s"`, or an SSE event as a doorbell for an `hx-get`) is worse
still: `Broadcast` suppresses a push when the pair is unchanged, so a quiet box currently
sends nothing, and either polling form costs a request and a full re-render per client per
second regardless.
