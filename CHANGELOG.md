# Changelog

## 0.1.0

### Added

- **The listing.** One row per listening process in the port range exe.dev proxies, served on
  the port the bare `<vm>.exe.xyz` hostname points at, pushed over SSE and updated within a
  second of anything changing. A row is one listening *process* rather than one port, so a
  process bound on both IPv4 and IPv6 is a single row while two processes sharing a port
  number are two, and each carries the command line behind the port, its working directory,
  its user, and how long it has been up. Links are built from the browser's own location, so
  they stay correct whether the page was reached through exe.dev's proxy or an SSH tunnel to
  the same port. `1`-`9` open the first nine from the keyboard. Listeners are found by
  polling, once a second, because the kernel offers no way to watch for a new listening
  socket: netlink's `inet_diag` answers a query rather than announcing one, and
  `/proc/net/tcp` cannot be watched.
- **What each port says it is.** Every listener is asked for a `GET /` and the row is titled
  by the `<title>` of whatever came back, falling back to the process name, with a port that
  answers something other than HTTP left unlinked. Probes run off the scan loop, so a port
  that accepts a connection and then says nothing does not hold every other row behind its
  timeout, and each is asked at an address the process actually holds rather than at
  loopback, since a process bound only to a LAN address has nothing on loopback to answer.
- **zellij sessions**, which have no port of their own and so appear nowhere in a listing
  built from sockets. A zellij web server is the one port not linked directly: arriving at
  its root does not land on a picker, it creates a session, so the existing sessions are
  listed individually instead.
- **A view for the VM's owner.** zellij session names and a VS Code Remote-SSH link are
  withheld from everyone but the address the VM is owned by, which exe.dev's proxy reports in
  `x-exedev-email` and exe.dev's reflection integration answers for at startup. The
  comparison fails closed, so a lookup that did not answer and an unauthenticated caller both
  match nobody. This is one degree less detail on the same page rather than confidentiality:
  the public half carries every command line on the box, and command lines routinely carry
  secrets, so the VM's own sharing settings are the only real boundary.
- **`install` and `serve`.** `install` writes a user systemd unit naming the interpreter that
  ran it and restarts the service, and reports a machine without `loginctl enable-linger`,
  where the unit is enabled and correct and still will not start until somebody logs in. It
  never fetches, builds, or manages a Python environment: whoever installed the package chose
  the interpreter, and `install` only points systemd at it. `serve` runs the same server in
  the foreground. Both take `--port`, default 8000, also read from `EXE_DEV_ATLAS_PORT`.
