from __future__ import annotations

import pytest

from exe_dev_atlas.reflection import vscode_url


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
def test_the_link_names_the_vm_and_the_folder_to_open(vm_name: str, directory: str, expected: str) -> None:
    # The VM's own name, not `location.hostname`: through an SSH tunnel that is `localhost`,
    # which is the one machine Remote-SSH must not connect to. And there is no way to say
    # "open no folder" in a URL, so the directory is always present.
    assert vscode_url(vm_name, directory) == expected


def test_an_unnamed_vm_yields_no_link_at_all() -> None:
    # Reflection not answering is the case this covers. Returning a URL built from an empty
    # name would offer `ssh-remote+.exe.xyz`, a host that does not exist.
    assert vscode_url("", "/home/dev") == ""
