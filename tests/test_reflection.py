from __future__ import annotations

import pytest

from exe_dev_atlas.reflection import ReflectionFailed
from exe_dev_atlas.reflection import parse_reflection
from exe_dev_atlas.reflection import vscode_url


class TestParsing:
    def test_the_document_names_the_vm_and_its_emoji(self) -> None:
        vm = parse_reflection(b'{"name": "cumulus", "emoji": "\\ud83d\\udcad"}')

        assert vm.name == "cumulus"
        assert vm.emoji == "\N{THOUGHT BALLOON}"

    def test_a_document_with_no_emoji_still_describes_the_vm(self) -> None:
        # The emoji is decoration, so its absence is not a reason to refuse to serve. The
        # name is the whole of what the tab, the heading, and the SSH link need.
        assert parse_reflection(b'{"name": "nimbus"}').emoji == ""

    def test_the_rest_of_the_document_is_ignored_rather_than_refused(self) -> None:
        # Reflection answers with the owner's email, the VM's integrations, and its tags as
        # well, and may answer with more tomorrow. Nothing here asked for any of it, so a
        # field this parse has never heard of is not a malformed document.
        vm = parse_reflection(b'{"name": "cirrus", "emoji": "\\u26c5", "paths": [{"path": "/email"}], "tags": []}')

        assert vm.name == "cirrus"

    @pytest.mark.parametrize(
        "published",
        [
            pytest.param(b'{"emoji": "\\u26c5"}', id="no-name"),
            pytest.param(b'{"name": ""}', id="an-empty-name"),
            pytest.param(b'{"name": null}', id="a-null-name"),
            pytest.param(b'{"name": 7}', id="a-name-that-is-not-a-string"),
            pytest.param(b'["cumulus"]', id="a-list"),
            pytest.param(b"not json at all", id="not-json"),
            pytest.param(b"", id="an-empty-body"),
        ],
    )
    def test_an_answer_that_names_no_vm_is_refused(self, published: bytes) -> None:
        # This is what makes the name non-empty everywhere downstream: nothing else builds a
        # `Reflection` from outside, so a page that could not say which box it is on is never
        # served at all.
        with pytest.raises(ReflectionFailed):
            parse_reflection(published)


class TestVsCodeLink:
    @pytest.mark.parametrize(
        ("vm_name", "directory", "expected"),
        [
            pytest.param(
                "cumulus",
                "/home/dev",
                "vscode://vscode-remote/ssh-remote+cumulus.exe.xyz/home/dev?windowId=_blank",
                id="home",
            ),
            pytest.param(
                "nimbus",
                "/srv/checkouts/project",
                "vscode://vscode-remote/ssh-remote+nimbus.exe.xyz/srv/checkouts/project?windowId=_blank",
                id="nested",
            ),
            pytest.param(
                "cirrus",
                "/",
                "vscode://vscode-remote/ssh-remote+cirrus.exe.xyz/?windowId=_blank",
                id="root",
            ),
        ],
    )
    def test_the_link_names_the_vm_and_the_folder_to_open(self, vm_name: str, directory: str, expected: str) -> None:
        # The VM's own name, not `location.hostname`: through an SSH tunnel that is
        # `localhost`, which is the one machine Remote-SSH must not connect to. And there is
        # no way to say "open no folder" in a URL, so the directory is always present.
        assert vscode_url(vm_name, directory) == expected
