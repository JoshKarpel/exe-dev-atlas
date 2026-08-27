from __future__ import annotations

import sys
from pathlib import Path

import pytest

from exe_dev_atlas.install import SERVICE
from exe_dev_atlas.install import can_run_the_atlas
from exe_dev_atlas.install import config_home
from exe_dev_atlas.install import converge
from exe_dev_atlas.install import running_executable
from exe_dev_atlas.install import unit_path
from exe_dev_atlas.install import unit_text
from exe_dev_atlas.processes import Ran

# Deliberately not this interpreter and not port 8000: a rendering that ignored its
# arguments and reported the running process would still pass against the real values.
INTERPRETER = Path("/opt/tools/exe-dev-atlas/bin/python")
PORT = 8123


class FakeSystemctl:
    """Records what was asked, so convergence is testable with no service manager present."""

    def __init__(self, *, fails: str = "") -> None:
        self.calls: list[tuple[str, ...]] = []
        self._fails = fails

    async def __call__(self, arguments: tuple[str, ...]) -> Ran:
        self.calls.append(arguments)
        failed = bool(self._fails) and arguments[0] == self._fails
        return Ran(
            command=("systemctl", "--user", *arguments),
            exit_code=1 if failed else 0,
            stdout="",
            stderr="refused" if failed else "",
            timed_out=False,
        )

    @property
    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls]


class TestUnitText:
    def test_the_rendered_unit_names_the_interpreter_and_the_port(self) -> None:
        # The module rather than a console script: `sys.executable` is always absolute and
        # always holds the package, while a console-script shim can be relocated out from
        # under the unit.
        text = unit_text(INTERPRETER, PORT)

        assert f"ExecStart={INTERPRETER} -m exe_dev_atlas serve --port {PORT}" in text

    def test_the_unit_carries_the_standard_system_path(self) -> None:
        # A user unit inherits none of a login shell's PATH, so a child that does look
        # something up on it would otherwise find nothing at all.
        path_line = next(
            line for line in unit_text(INTERPRETER, PORT).splitlines() if line.startswith("Environment=PATH=")
        )
        entries = path_line.removeprefix("Environment=PATH=").split(":")

        assert "/usr/sbin" in entries
        assert "/bin" in entries

    def test_the_unit_restarts_itself_so_a_scan_thread_dying_is_not_terminal(self) -> None:
        text = unit_text(INTERPRETER, PORT)

        assert "Restart=always" in text
        assert "WantedBy=default.target" in text

    def test_two_ports_render_differently(self) -> None:
        # The guard on `converge` writing only when the text differs: if the port were
        # dropped from the rendering, changing it would silently converge onto the old one.
        assert unit_text(INTERPRETER, 8123) != unit_text(INTERPRETER, 9001)


class TestRunningExecutable:
    def test_the_interpreter_named_is_the_one_actually_running(self) -> None:
        assert running_executable() == Path(sys.executable)

    def test_the_interpreter_is_not_resolved_through_its_symlink(self) -> None:
        """
        A venv's `bin/python` is a symlink to the base interpreter it was built from, and that
        base has none of the venv's packages importable. Resolving it therefore yields an
        interpreter that answers `No module named exe_dev_atlas`, so the unit installs
        cleanly and then crash-loops at every start.

        This only *bites* inside a venv, which is where the atlas is installed in every real
        deployment, and the test suite runs in one too. Where it does not (a system Python
        with no symlink to follow), the resolved and unresolved paths agree and the assertion
        below is trivially satisfied rather than wrong.
        """
        resolved = Path(sys.executable).resolve()
        if resolved == Path(sys.executable):
            pytest.skip("this interpreter is not reached through a symlink, so there is nothing to resolve away")

        assert running_executable() != resolved

    async def test_the_named_interpreter_can_actually_import_the_package(self) -> None:
        # The property the whole design rests on, driven rather than reasoned about: the unit
        # names this interpreter, so this interpreter must be able to run `-m exe_dev_atlas`.
        assert await can_run_the_atlas(running_executable()) is True

    async def test_an_interpreter_without_the_package_is_rejected(self) -> None:
        # What the install-time check exists to catch. `/bin/sh` stands in for any executable
        # that is not a Python holding this package, which is what a resolved venv symlink
        # amounts to.
        assert await can_run_the_atlas(Path("/bin/sh")) is False

    async def test_an_executable_that_does_not_exist_is_rejected(self) -> None:
        assert await can_run_the_atlas(Path("/nonexistent/python")) is False


class TestConfigHome:
    def test_an_explicit_xdg_config_home_is_used_as_given(self) -> None:
        assert config_home({"XDG_CONFIG_HOME": "/custom/config"}) == Path("/custom/config")

    def test_an_absent_xdg_config_home_falls_back_to_the_conventional_location(self) -> None:
        assert config_home({}) == Path.home() / ".config"

    def test_an_empty_xdg_config_home_is_treated_as_absent(self) -> None:
        assert config_home({"XDG_CONFIG_HOME": ""}) == Path.home() / ".config"

    def test_the_unit_lands_under_the_user_systemd_directory(self) -> None:
        assert unit_path(Path("/custom/config")) == Path(f"/custom/config/systemd/user/{SERVICE}.service")


class TestConverge:
    async def test_a_first_install_writes_the_unit_and_reloads(self, tmp_path: Path) -> None:
        unit = unit_path(tmp_path)
        systemctl = FakeSystemctl()

        converged = await converge(INTERPRETER, unit, PORT, systemctl)

        assert converged.unit_changed is True
        assert unit.read_text() == unit_text(INTERPRETER, PORT)
        assert systemctl.verbs == ["daemon-reload", "enable", "restart"]

    async def test_an_unchanged_unit_is_not_rewritten_and_the_manager_is_not_reloaded(self, tmp_path: Path) -> None:
        unit = unit_path(tmp_path)
        await converge(INTERPRETER, unit, PORT, FakeSystemctl())

        systemctl = FakeSystemctl()
        converged = await converge(INTERPRETER, unit, PORT, systemctl)

        assert converged.unit_changed is False
        assert "daemon-reload" not in systemctl.verbs

    async def test_the_restart_happens_even_when_the_unit_text_is_already_current(self, tmp_path: Path) -> None:
        # The whole point of running this after an upgrade: an upgrade in place renders an
        # identical unit, so only the restart puts the new code in front of anything. A
        # difference-gated restart would report success while serving the old build.
        unit = unit_path(tmp_path)
        await converge(INTERPRETER, unit, PORT, FakeSystemctl())

        systemctl = FakeSystemctl()
        await converge(INTERPRETER, unit, PORT, systemctl)

        assert systemctl.verbs == ["enable", "restart"]

    async def test_changing_the_port_rewrites_the_unit_and_reloads(self, tmp_path: Path) -> None:
        unit = unit_path(tmp_path)
        await converge(INTERPRETER, unit, 8123, FakeSystemctl())

        systemctl = FakeSystemctl()
        converged = await converge(INTERPRETER, unit, 9001, systemctl)

        assert converged.unit_changed is True
        assert "--port 9001" in unit.read_text()
        assert systemctl.verbs == ["daemon-reload", "enable", "restart"]

    async def test_changing_the_interpreter_rewrites_the_unit(self, tmp_path: Path) -> None:
        unit = unit_path(tmp_path)
        await converge(INTERPRETER, unit, PORT, FakeSystemctl())

        moved = Path("/opt/other/bin/python")
        converged = await converge(moved, unit, PORT, FakeSystemctl())

        assert converged.unit_changed is True
        assert str(moved) in unit.read_text()

    async def test_enabling_is_unconditional_so_an_unenabled_unit_is_always_fixed(self, tmp_path: Path) -> None:
        unit = unit_path(tmp_path)
        await converge(INTERPRETER, unit, PORT, FakeSystemctl())

        systemctl = FakeSystemctl()
        await converge(INTERPRETER, unit, PORT, systemctl)

        assert ("enable", SERVICE) in systemctl.calls

    async def test_the_parent_directory_is_created_when_it_does_not_exist(self, tmp_path: Path) -> None:
        unit = unit_path(tmp_path / "never" / "existed")

        await converge(INTERPRETER, unit, PORT, FakeSystemctl())

        assert unit.is_file()

    @pytest.mark.parametrize("verb", ["daemon-reload", "enable", "restart"])
    async def test_a_systemctl_refusal_is_raised_rather_than_reported_as_success(
        self, tmp_path: Path, verb: str
    ) -> None:
        # An install that swallowed these would tell the operator the service is running
        # when it is not, which is the one thing this command must never do.
        unit = unit_path(tmp_path)

        with pytest.raises(Exception, match="exited 1"):
            await converge(INTERPRETER, unit, PORT, FakeSystemctl(fails=verb))
