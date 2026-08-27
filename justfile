#!/usr/bin/env just --justfile

set ignore-comments

# This checkout installs beside the atlas that holds the default unit and the default port,
# so working on it never takes down the one the box is actually fronted by. The service name
# is derived rather than written out twice: it is what `install` renders from the suffix.
DEV_SUFFIX := "dev"
DEV_PORT := "8001"
DEV_SERVICE := "exe-dev-atlas-" + DEV_SUFFIX

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

[doc('Run the atlas in the foreground')]
serve *args:
    uv run exe-dev-atlas serve {{ args }}

[doc('Install this checkout as the dev atlas, beside whatever holds the default unit')]
install *args:
    uv run exe-dev-atlas install --systemd-unit-suffix {{ DEV_SUFFIX }} --port {{ DEV_PORT }} {{ args }}

[doc('Follow the dev atlas log')]
logs *args:
    journalctl --user -u {{ DEV_SERVICE }} -f {{ args }}

[doc("Capture the README's screenshots of this machine, in both colour schemes")]
screenshot *args:
    uv run --script scripts/screenshot.py {{ args }}
