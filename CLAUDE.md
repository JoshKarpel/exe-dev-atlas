# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```console
$ just setup            # uv sync + install pre-commit as a git hook
$ just test             # mypy, then pytest
$ just test tests/test_scan.py::test_name   # extra args go straight to pytest
$ just check            # pre-commit over all files, then mypy
$ just serve --port 8123  # foreground, on a non-default port
$ just install          # this checkout as exe-dev-atlas-dev on port 8001
$ just logs             # journalctl --user -u exe-dev-atlas-dev -f
$ just screenshot       # regenerate the README's images from this machine
```

`just install` and `just logs` are about the *dev* unit, never the default one: this box also
runs a published atlas on `exe-dev-atlas` and port 8000, converged daily by a timer the
dotfiles own, and a checkout that installed over it would take the box's own front door down
with every experiment. The suffix and the port are variables at the top of the `justfile`.

The README's screenshots are generated, not hand-taken, so a change to `page.py`, `atlas.css`,
or `atlas.js` that alters the layout means running `just screenshot` in the same change. It
serves its own atlas and writes both colour schemes.
Its playwright dependency is in a PEP 723 header rather than the dev group, because CI
syncs that group and this is 140 MB of browser driver.

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

[`psutil`](https://psutil.readthedocs.io/) is where every fact about a socket or a process
comes from. Nothing shells out to `ss`, and the reason is worth keeping: its table has to be
recovered from padded columns, `--json` is missing from builds as recent as iproute2 6.1, and
it renders a wildcard bind as `*` on some builds and `[::]` on others, so `probe_address`
would be matching on a rendering rather than on an address. psutil answers `0.0.0.0` and `::`.
It ships no `py.typed`, so `types-psutil` is a dev dependency; keep the two versions in step.

Python 3.14 only. The code uses unparenthesized multi-exception `except OSError, ValueError:`
(PEP 758) in several places; that is valid 3.14 syntax, not the Python 2 bind form.

## Releases

The `version` in `pyproject.toml` is the one that gets published. Publishing runs on a
published GitHub release and builds the checked-out tree as it stands, so cutting a release
means bumping that field and opening a `CHANGELOG.md` section under it first, then tagging
the commit that carries both. The tag is not read and nothing checks the three agree. Upload
is PyPI trusted publishing from the `pypi` environment, which has to be registered on the
PyPI project before a release can land.

A `CHANGELOG.md` entry describes what changed *between releases*, so a `Fixed` entry claims
somebody on the last release could have hit the bug. A fix to code that has not shipped
belongs in that feature's own `Added` entry if it changed anything a reader can see, and
nowhere if it did not.

CI runs on Linux alone, and that is not a cost-saving: `TestReadingThisMachine` binds a real
socket and reads this very process, and every listener fact under it comes from `/proc`.

## Architecture

### One scan loop, one payload, no authorization decision

`app.build_app` is the composition root. Its lifespan builds an `Atlas` (a `Broadcast` and the
pre-rendered page `Response`) once, then binds `scan.scan_forever` to the server's lifetime
with `background_task`. Handlers see nothing but the `Atlas`.

`scan_once` reads the machine and publishes: `read_listeners` for sockets, `read_process` for
the processes behind them, `zellij list-sessions` for session servers, gathered rather than
awaited in turn so one hung session server does not hold every row behind its timeout. The
first two are synchronous inside an async loop on purpose: both are `/proc` reads, memory
formatting with no device behind it to block on, and at a few milliseconds once a second
`asyncio.to_thread` would cost more in dispatch than the reads take. It serializes one JSON
payload and hands it to `Broadcast.publish`, which only bumps its version when the payload
differs from the last, so a quiet box pushes nothing. Serializing there rather than in the
handler is what makes the cost one per scan however many connections are held.

`scan_forever` is the cadence around it, and it must **not** die on a bad scan: nothing
watches this task, so a page holding the last payload keeps its heartbeated connection and
reads "live" over a listing that stopped moving. A `psutil.Error` is logged and the next scan
retries; anything else is logged on the way out, since `background_task` surfaces a task's
exception only when the server shuts down. `main.serve` configures logging to stderr, which
under the unit is the journal (`just logs`).

`read_listeners` is injected into `scan_once` and `scan_forever` as a `ReadListeners`
callable, so a test drives a scan over a listing it wrote rather than over whatever this
machine is running.

Polling is deliberate, not a stopgap: the kernel offers no way to watch for a new listening
socket.

A row is one *listening process*, not one port: `group_listeners` groups on `(port, pid)`, so
one pid bound on IPv4 and IPv6 is one row while two processes sharing a port number are two.
Anything keying rows by port alone (`atlas.js` looks its elements up by `port/pid`) collides
the moment that happens. A socket owned by another user arrives with `pid=None`, since its
`/proc/<pid>/fd` is not ours to read, so several of those on one port do collapse into one
row.

Every connection is served the same payload, and the app reads no header to decide anything.
There was an owner-only half once, holding zellij session names and the VS Code link behind
exe.dev's `x-exedev-email`; it withheld nothing that was not already reachable, since the
session server it named sits on the same proxied hostname under the same sharing grant and
the VS Code link needs SSH access to be worth anything. What protects this page is the VM's
sharing settings and nothing else, which is what the README says where somebody deciding how
to share a VM will read it. Every command line on the box crosses the wire, so a feature that
re-splits the payload by caller is answering the wrong question.

### What must not cross the wire

`listeners.Process` carries `executable` (the binary the process is running), which `zellij.read_sessions`
needs to invoke the exact binary that is serving. `scan.build_row` deliberately drops it: a
`Row` is serialized straight to every connected browser. Keep that split when adding fields.

`Row.as_dict` names every field that crosses, rather than reaching for `dataclasses.asdict`,
which recurses and deep-copies each value on the way out. What that buys is the omission of
`executable` being visible at the point it is decided; what it costs is a field list nothing
derives, so a field added to `Row` and forgotten here never reaches the browser. That failure
is silent and permanent (`atlas.js` reads the payload unguarded, so the first message throws,
the page freezes on stale rows, and `state` goes on reading "live"), which is why
`test_every_field_of_a_row_reaches_the_browser` pins the two together. Adding a field to
`Row` means adding it to `as_dict`, and the suite says so.

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

A probe asks an address the process actually holds, chosen by `probe_address`, rather than
loopback. Rows are per listening process, so two processes on one port number would otherwise
both be described by whichever of them holds loopback, and a process bound only to a LAN
address has nothing on loopback at all and would read as a web server that does not answer
HTTP. A wildcard bind is asked on the loopback of its own family.

A result stands for `PROBE_INTERVAL`, whatever it said. A `<title>`, a status and a `server`
are claims about the moment the probe ran, and the row around them updates every second, so a
result held for the process's lifetime is stale in a way nothing on the page distinguishes
from fresh. One flat cadence rather than a ladder that earns an answered port a slower one:
`PROBE_TIMEOUT` is short enough that a dev server compiling a route on demand overruns it, and
under a ladder that one slow answer both demoted a working server and then held it demoted for
the length of the slow cadence. What the flat cadence costs is a request per listener per
interval for as long as the page is up, which is why the interval is seconds rather than one
scan.

### Install is convergence, not packaging

`install.py` renders a user systemd unit naming `sys.executable`, unresolved (resolving a
venv's `bin/python` symlink yields a base interpreter that cannot import the package). It
never fetches or builds an environment. In `converge`, the `daemon-reload` is conditional on
the unit text changing but the `restart` is **unconditional**: an upgrade in place renders
identical text, so the restart is the only thing that puts new code in front of anything.

A `Unit` is everything that can differ between two atlases on one machine, and `converge`
takes one rather than the settings loose, so adding an install-time setting is a field rather
than another argument threaded through `main.install`. `unit.service` is what every
`systemctl` call names, which is what keeps an install off every other unit on the box.
`service_name` is the only thing that builds one, and it **parses** rather than accepts: the
suffix is interpolated into a filename under `~/.config/systemd/user`, so `../ssh-agent` has
to be refused before anything is written. The package name is always the prefix, so
`systemctl --user list-units 'exe-dev-atlas*'` answers "what atlases are on this box".

`WantedBy=default.target` starts the unit when the *user manager* starts, which without
`loginctl enable-linger <user>` is at first login rather than at boot. `install` checks this
and says so if it is off, because the failure is otherwise invisible: the unit is enabled, the
file is correct, and nothing is running.

`systemctl` is injected as a `Systemctl` callable so tests drive convergence without a
service manager.

### Functional core

`group_listeners`, `build_row`, `Row.as_dict`, `Unit.text`, `service_name`, `is_zellij_web`,
`format_probe_title`, `probe_address`, `probe_url`, and `page.shell` are pure and tested
directly. The I/O shell around them is thin: `processes.run` returns a `Ran` value (a timeout
and a cancellation both kill the child; a timeout is an outcome rather than an exception, and
`.checked()` is the loud version), and reflection and process reads alike return empty values
that every caller is written to treat as an honest "no answer".

`read_listeners`, `read_process`, and `read_environ` are the psutil boundary, and nothing but
`TestReadingThisMachine` pins the field names they ask for: those tests bind a real socket and
read this very process, because a psutil release that renamed `laddr` or stopped answering
`create_time` would leave every pure test green while the page rendered blank rows. The start
time is asserted to be *stable across two reads*, which is the property the payload diff
depends on: psutil derives it from `starttime` ticks plus `/proc/stat`'s `btime`, both
integers the kernel settled, where `time.time()` minus `/proc/uptime` wanders across a 10ms
band and republishes the whole payload once a second.

### Frontend

`static/atlas.js` renders everything from the SSE payload and builds links from
`location.hostname`, so they stay correct through the exe.dev proxy or an SSH tunnel alike.
The one exception is the VS Code Remote-SSH link, built server-side from the reflection VM
name because it names a host to SSH to rather than one to fetch from.

The `+ new session` link on a zellij web server's row is the only thing the page offers that
creates something rather than pointing at what is already running, and both halves of keeping
that honest live in `atlas.js`: it is never passed to `offer`, so none of the `1`-`9` digits
reach it, and the row's own anchor stays inert as before. `row.sessions` is what marks a row
as a session server at all, by its *presence* rather than its length: a server serving nothing
carries an empty list, and that is exactly the row the link is there for. So `scan_once` must
keep emitting the key for every session server, empty tuple included.

`page.py` server-renders a shell that is constant for the process: the element ids the script
looks up, two asset links, and the VM's name in `<title>`, which is not per-request because
reflection answers once at startup. `app.build_router` serves `static/` from an `inventory`
walked once at startup, so nothing may write into that directory while the process runs.

The header is the VM's identity: `#emblem` holds its emoji and `#vm` its name, both from
reflection and written by `applyIdentity` when the first payload lands. The shell renders
"Atlas" into `#emblem` and hides `#vm`, which is what a box off exe.dev keeps, so neither is
ever a blank gap. `#host` stays what it was, the hostname the reader actually reached: through
a tunnel that is `localhost`, which identifies no VM, and is why the name beside it is read
from reflection rather than from the URL. `atlas.js` rewrites the title for that same case,
where reflection named no VM and only the browser knows what to call the box.

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
