from __future__ import annotations

import pytest

from exe_dev_atlas.zellij import is_zellij_web


@pytest.mark.parametrize(
    "command_line",
    [
        pytest.param("zellij web", id="bare"),
        pytest.param("/home/dev/.local/bin/zellij web", id="absolute-path"),
        pytest.param("zellij web --port 3000", id="with-flags"),
        pytest.param("zellij --config /etc/zellij.kdl web", id="flags-before-the-subcommand"),
    ],
)
def test_a_zellij_web_server_is_recognised(command_line: str) -> None:
    assert is_zellij_web("zellij", command_line) is True


@pytest.mark.parametrize(
    ("command_name", "command_line"),
    [
        pytest.param("zellij", "zellij attach cumulus", id="attach-not-web"),
        pytest.param("zellij", "zellij list-sessions", id="list-sessions"),
        pytest.param("zellij", "zellij", id="no-subcommand"),
        # `web` has to be its own argument. A substring match would call each of these one.
        pytest.param("zellij", "zellij attach webserver", id="web-as-a-name-prefix"),
        pytest.param("zellij", "zellij run mywebapp", id="web-inside-a-word"),
        pytest.param("node", "node web", id="a-different-program-with-a-web-argument"),
        pytest.param("zellij-web-proxy", "zellij-web-proxy web", id="a-different-program-named-like-it"),
    ],
)
def test_anything_that_is_not_a_zellij_web_server_is_not_recognised(command_name: str, command_line: str) -> None:
    assert is_zellij_web(command_name, command_line) is False


def test_a_listener_with_no_readable_process_is_not_a_session_server() -> None:
    # A socket owned by another user comes through with both fields empty, and must not be
    # guessed at either way.
    assert is_zellij_web("", "") is False
