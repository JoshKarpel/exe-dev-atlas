from __future__ import annotations

import pytest

from exe_dev_atlas.reflection import vscode_url


def test_a_named_vm_yields_a_remote_ssh_link_to_the_given_directory() -> None:
    url = vscode_url("cumulus", "/home/dev")

    assert url == "vscode://vscode-remote/ssh-remote+cumulus.exe.xyz/home/dev?windowId=_blank"


def test_the_link_names_the_vm_rather_than_whatever_host_the_browser_used() -> None:
    # The one link on the page that must not be built from `location.hostname`: through an
    # SSH tunnel that is `localhost`, which is the machine Remote-SSH must not connect to.
    assert "cirrus.exe.xyz" in vscode_url("cirrus", "/srv/app")


def test_an_unnamed_vm_yields_no_link_at_all() -> None:
    # Reflection not answering is the case this covers. Returning a URL built from an empty
    # name would offer `ssh-remote+.exe.xyz`, a host that does not exist.
    assert vscode_url("", "/home/dev") == ""


@pytest.mark.parametrize(
    "directory",
    [
        pytest.param("/home/dev", id="home"),
        pytest.param("/srv/checkouts/project", id="nested"),
        pytest.param("/", id="root"),
    ],
)
def test_the_directory_is_carried_through_as_the_folder_to_open(directory: str) -> None:
    # There is no way to say "open no folder" in a URL, so the directory is always present.
    assert vscode_url("nimbus", directory).endswith(f"nimbus.exe.xyz{directory}?windowId=_blank")
