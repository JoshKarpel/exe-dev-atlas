#!/usr/bin/env just --justfile

set ignore-comments

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

[doc('Install the user systemd unit on this machine and restart the atlas onto this interpreter')]
install *args:
    uv run exe-dev-atlas install {{ args }}

[doc('Follow the atlas log')]
logs *args:
    journalctl --user -u exe-dev-atlas -f {{ args }}
