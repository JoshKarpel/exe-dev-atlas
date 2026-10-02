#!/usr/bin/env just --justfile

set ignore-comments

# This checkout installs beside the atlas that holds the default unit and the default port,
# so working on it never takes down the one the box is actually fronted by. The unit name is
# spelled out rather than asked of `install`, which means it is a second copy of the prefix
# that `install.SERVICE` owns: `just logs` against a name nothing installed prints nothing and
# exits 0, so keep the two in step by hand.
DEV_SUFFIX := "dev"
DEV_PORT := "8001"
DEV_SERVICE := "exe-dev-atlas-" + DEV_SUFFIX

# Off both ports above, so a foreground atlas runs beside the box's own and the dev unit.
SERVE_PORT := "8123"

[default]
[doc('List available recipes')]
list:
    just --list

alias l := list

[doc('Prepare a fresh clone: dependencies, and pre-commit as a git hook')]
setup:
    uv sync
    uv run pre-commit install

[doc('Run type checking and tests')]
test *args:
    uv run mypy
    uv run pytest {{ args }}

alias t := test

[doc('Format and lint')]
check:
    uv run pre-commit run --all-files
    uv run mypy

# Watching all of `src` rather than only its Python: `static/` is inventoried once at startup,
# so a stylesheet or script edit needs a restart to be served at all.
[doc('Run the atlas in the foreground, restarting on code changes, on a port of its own unless given one')]
serve port=SERVE_PORT *args:
    exec uv run watchfiles --target-type command 'exe-dev-atlas serve --port {{ port }} {{ args }}' src

[doc('Install this checkout as the dev atlas, beside whatever holds the default unit')]
install *args:
    uv run exe-dev-atlas install --systemd-unit-suffix {{ DEV_SUFFIX }} --port {{ DEV_PORT }} {{ args }}

[doc('Follow the dev atlas log')]
logs *args:
    journalctl --user -u {{ DEV_SERVICE }} -f {{ args }}

[doc("Capture the README's screenshots of this machine, in both colour schemes")]
screenshot *args:
    uv run --script scripts/screenshot.py {{ args }}
