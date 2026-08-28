from __future__ import annotations

import shlex
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from exe_dev_atlas import app
from exe_dev_atlas.install import SERVICE
from exe_dev_atlas.install import BadSuffix
from exe_dev_atlas.install import Converged
from exe_dev_atlas.install import Systemctl
from exe_dev_atlas.install import Unit
from exe_dev_atlas.install import can_run_the_atlas
from exe_dev_atlas.install import config_home
from exe_dev_atlas.install import converge
from exe_dev_atlas.install import running_executable
from exe_dev_atlas.install import service_name
from exe_dev_atlas.install import unit_path
from exe_dev_atlas.main import exe_dev_atlas
from exe_dev_atlas.processes import Ran

# Deliberately not this interpreter and not port 8000: a rendering that ignored its
# arguments and reported the running process would still pass against the real values.
INTERPRETER = Path("/opt/tools/exe-dev-atlas/bin/python")
PORT = 8123


def unit_for(
    config_home: Path,
    *,
    service: str = SERVICE,
    executable: Path = INTERPRETER,
    port: int = PORT,
    vscode_link: bool = True,
) -> Unit:
    """The unit an install would converge, under a config home a test may write into."""
    return Unit(
        service=service,
        config_home=config_home,
        executable=executable,
        port=port,
        vscode_link=vscode_link,
    )


class FakeSystemctl:
    """
    Records what was asked, so convergence is testable with no service manager present.

    `state` is what a `show` is answered with, which is how a test says whether the service
    that was just restarted is still up a moment later.
    """

    def __init__(self, *, fails: str = "", state: str = "active") -> None:
        self.calls: list[tuple[str, ...]] = []
        self._fails = fails
        self._state = state

    async def __call__(self, arguments: tuple[str, ...]) -> Ran:
        self.calls.append(arguments)
        failed = bool(self._fails) and arguments[0] == self._fails
        return Ran(
            command=("systemctl", "--user", *arguments),
            exit_code=1 if failed else 0,
            stdout=f"{self._state}\n" if arguments[0] == "show" else "",
            stderr="refused" if failed else "",
            timed_out=False,
        )

    @property
    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls]


async def converge_now(unit: Unit, systemctl: Systemctl) -> Converged:
    """
    Converge with no settle window, since there is no service here to watch fail.

    The window is what a real install spends letting a `Type=exec` start fail before it
    reports the service running; a fake answers the same thing whenever it is asked.
    """
    return await converge(unit, systemctl, settle=timedelta(0))


class TestUnitText:
    def test_the_rendered_unit_names_the_interpreter_and_the_port(self, tmp_path: Path) -> None:
        # The module rather than a console script: `sys.executable` is always absolute and
        # always holds the package, while a console-script shim can be relocated out from
        # under the unit.
        text = unit_for(tmp_path).text

        assert f"ExecStart={INTERPRETER} -m exe_dev_atlas serve --port {PORT}" in text

    def test_the_unit_carries_the_standard_system_path(self, tmp_path: Path) -> None:
        # A user unit inherits none of a login shell's PATH, so a child that does look
        # something up on it would otherwise find nothing at all.
        path_line = next(line for line in unit_for(tmp_path).text.splitlines() if line.startswith("Environment=PATH="))
        entries = path_line.removeprefix("Environment=PATH=").split(":")

        assert "/usr/sbin" in entries
        assert "/bin" in entries

    def test_the_unit_restarts_itself_so_a_scan_thread_dying_is_not_terminal(self, tmp_path: Path) -> None:
        text = unit_for(tmp_path).text

        assert "Restart=always" in text
        assert "WantedBy=default.target" in text

    def test_two_ports_render_differently(self, tmp_path: Path) -> None:
        # The guard on `converge` writing only when the text differs: if the port were
        # dropped from the rendering, changing it would silently converge onto the old one.
        assert unit_for(tmp_path, port=8123).text != unit_for(tmp_path, port=9001).text

    def test_the_description_names_the_port_so_two_installs_read_apart(self, tmp_path: Path) -> None:
        # What `systemctl --user list-units` shows, which on a machine holding two atlases is
        # the only thing on that line telling them apart.
        assert "Description=" in unit_for(tmp_path, port=9001).text
        assert "port 9001" in unit_for(tmp_path, port=9001).text

    @pytest.mark.parametrize(
        ("vscode_link", "flag"),
        [pytest.param(True, "--vs-code-link", id="on"), pytest.param(False, "--no-vs-code-link", id="off")],
    )
    def test_the_unit_states_which_way_the_vs_code_link_was_asked_for(
        self, tmp_path: Path, vscode_link: bool, flag: str
    ) -> None:
        # Named either way rather than only when withheld, so the unit records the choice
        # instead of leaning on whatever `serve` defaults to when it is next started.
        text = unit_for(tmp_path, vscode_link=vscode_link).text

        assert f"ExecStart={INTERPRETER} -m exe_dev_atlas serve --port {PORT} {flag}" in text


class TestTheUnitStartsACommandThisCliAccepts:
    """
    The `ExecStart` line, fed back through the CLI it names, arriving as the settings it came
    from.

    The unit spells `serve`'s options as literals in another module, and the assertions above
    compare those literals against themselves: renaming `--vs-code-link` in `main.py` leaves
    them green while every installed unit crash-loops on an unrecognised flag. Running the
    rendered arguments through the real command is what makes that a failing test here rather
    than a `failed` state the install reports and an operator reads the journal for.
    """

    @pytest.mark.parametrize("vscode_link", [True, False], ids=["link-on", "link-off"])
    def test_the_rendered_arguments_parse_back_to_the_settings_that_rendered_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vscode_link: bool
    ) -> None:
        unit = unit_for(tmp_path, port=9001, vscode_link=vscode_link)
        asked: dict[str, object] = {}

        def record(port: int, *, vscode_link: bool) -> None:
            asked.update(port=port, vscode_link=vscode_link)

        # Patched where `main.serve` looks the name up, and only so the command returns
        # instead of binding a port: what is under test is the parse, not the server.
        monkeypatch.setattr(app, "serve_until_stopped", record)

        exec_start = next(line for line in unit.text.splitlines() if line.startswith("ExecStart="))
        arguments = shlex.split(exec_start.removeprefix("ExecStart="))
        result = CliRunner().invoke(exe_dev_atlas, arguments[arguments.index("serve") :])

        assert result.exit_code == 0, result.output
        assert asked == {"port": unit.port, "vscode_link": unit.vscode_link}


class TestServiceName:
    def test_an_install_that_named_no_suffix_is_the_atlas_on_this_machine(self) -> None:
        assert service_name("") == SERVICE

    @pytest.mark.parametrize("suffix", ["dev", "work-2", "scratch_box", "7"])
    def test_a_suffixed_install_keeps_the_package_name_as_its_prefix(self, suffix: str) -> None:
        # What makes `systemctl --user list-units 'exe-dev-atlas*'` an answer to "what
        # atlases are on this box".
        assert service_name(suffix) == f"{SERVICE}-{suffix}"

    @pytest.mark.parametrize(
        "suffix",
        [
            pytest.param("../ssh-agent", id="a-path-out-of-the-unit-directory"),
            pytest.param("dev/one", id="a-separator"),
            pytest.param("dev one", id="a-space"),
            pytest.param("dev@1", id="a-template-instance"),
            pytest.param("dev.service", id="a-second-extension"),
            pytest.param("-dev", id="a-leading-hyphen"),
            pytest.param("dév", id="a-letter-outside-ascii"),
        ],
    )
    def test_a_suffix_a_unit_name_could_not_carry_is_refused(self, suffix: str) -> None:
        # The first two are the ones that matter: a suffix is interpolated into a filename
        # under `~/.config/systemd/user`, so anything reaching out of that directory would
        # let a typo write over and restart a unit that has nothing to do with this.
        with pytest.raises(BadSuffix):
            service_name(suffix)


class TestUnitPath:
    def test_a_suffixed_install_lands_beside_the_default_one_rather_than_on_it(self, tmp_path: Path) -> None:
        assert unit_path(tmp_path, service_name("dev")) != unit_path(tmp_path, SERVICE)
        assert unit_path(tmp_path, service_name("dev")).name == "exe-dev-atlas-dev.service"


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
        assert unit_path(Path("/custom/config"), SERVICE) == Path(f"/custom/config/systemd/user/{SERVICE}.service")


class TestConverge:
    async def test_a_first_install_writes_the_unit_and_reloads(self, tmp_path: Path) -> None:
        unit = unit_for(tmp_path)
        systemctl = FakeSystemctl()

        converged = await converge_now(unit, systemctl)

        assert converged.text_changed is True
        assert unit.path.read_text() == unit.text
        assert systemctl.verbs == ["daemon-reload", "enable", "restart", "show"]

    async def test_an_unchanged_unit_is_not_rewritten_and_the_manager_is_not_reloaded(self, tmp_path: Path) -> None:
        unit = unit_for(tmp_path)
        await converge_now(unit, FakeSystemctl())

        systemctl = FakeSystemctl()
        converged = await converge_now(unit, systemctl)

        assert converged.text_changed is False
        assert "daemon-reload" not in systemctl.verbs

    async def test_the_restart_happens_even_when_the_unit_text_is_already_current(self, tmp_path: Path) -> None:
        # The whole point of running this after an upgrade: an upgrade in place renders an
        # identical unit, so only the restart puts the new code in front of anything. A
        # difference-gated restart would report success while serving the old build.
        unit = unit_for(tmp_path)
        await converge_now(unit, FakeSystemctl())

        systemctl = FakeSystemctl()
        await converge_now(unit, systemctl)

        assert systemctl.verbs == ["enable", "restart", "show"]

    async def test_changing_the_port_rewrites_the_unit_and_reloads(self, tmp_path: Path) -> None:
        await converge_now(unit_for(tmp_path, port=8123), FakeSystemctl())

        systemctl = FakeSystemctl()
        moved = unit_for(tmp_path, port=9001)
        converged = await converge_now(moved, systemctl)

        assert converged.text_changed is True
        assert "--port 9001" in moved.path.read_text()
        assert systemctl.verbs == ["daemon-reload", "enable", "restart", "show"]

    async def test_withdrawing_the_vs_code_link_rewrites_the_unit_and_reloads(self, tmp_path: Path) -> None:
        # `install --no-vs-code-link` on a machine already running with the link is the whole
        # way the flag reaches the service, so a rendering that dropped it would report a
        # successful install and go on serving the link.
        await converge_now(unit_for(tmp_path, vscode_link=True), FakeSystemctl())

        systemctl = FakeSystemctl()
        withheld = unit_for(tmp_path, vscode_link=False)
        converged = await converge_now(withheld, systemctl)

        assert converged.text_changed is True
        assert "--no-vs-code-link" in withheld.path.read_text()
        assert systemctl.verbs == ["daemon-reload", "enable", "restart", "show"]

    async def test_changing_the_interpreter_rewrites_the_unit(self, tmp_path: Path) -> None:
        await converge_now(unit_for(tmp_path), FakeSystemctl())

        moved = unit_for(tmp_path, executable=Path("/opt/other/bin/python"))
        converged = await converge_now(moved, FakeSystemctl())

        assert converged.text_changed is True
        assert "/opt/other/bin/python" in moved.path.read_text()

    async def test_a_suffixed_install_leaves_the_default_one_running_and_untouched(self, tmp_path: Path) -> None:
        # The whole point of the suffix: two atlases on one machine, and installing either
        # one reaches its own unit alone. A rendering that kept the default service name
        # would overwrite the file and restart the service somebody else's dotfiles own.
        default = unit_for(tmp_path)
        await converge_now(default, FakeSystemctl())

        systemctl = FakeSystemctl()
        beside = unit_for(tmp_path, service=service_name("dev"), port=9001)
        converged = await converge_now(beside, systemctl)

        assert converged.text_changed is True
        assert beside.path.read_text() == beside.text
        assert default.path.read_text() == default.text
        assert systemctl.calls == [
            ("daemon-reload",),
            ("enable", "exe-dev-atlas-dev"),
            ("restart", "exe-dev-atlas-dev"),
            ("show", "exe-dev-atlas-dev", "--property=ActiveState", "--value"),
        ]

    async def test_enabling_is_unconditional_so_an_unenabled_unit_is_always_fixed(self, tmp_path: Path) -> None:
        unit = unit_for(tmp_path)
        await converge_now(unit, FakeSystemctl())

        systemctl = FakeSystemctl()
        await converge_now(unit, systemctl)

        assert ("enable", SERVICE) in systemctl.calls

    async def test_the_parent_directory_is_created_when_it_does_not_exist(self, tmp_path: Path) -> None:
        unit = unit_for(tmp_path / "never" / "existed")

        await converge_now(unit, FakeSystemctl())

        assert unit.path.is_file()

    async def test_a_service_that_is_up_after_the_restart_is_reported_as_running(self, tmp_path: Path) -> None:
        converged = await converge_now(unit_for(tmp_path), FakeSystemctl(state="active"))

        assert converged.state == "active"
        assert converged.is_running is True

    @pytest.mark.parametrize("state", ["failed", "activating", "inactive", ""])
    async def test_a_service_that_did_not_stay_up_is_not_reported_as_running(self, tmp_path: Path, state: str) -> None:
        # The gap this closes: the unit is `Type=exec`, so its start job completes at `execve`
        # and a `restart` succeeds against a process that exits a moment later, which is what
        # a second atlas told to bind a port the first one already holds does. Reporting from
        # the restart alone is how an install comes to claim a service that never bound
        # anything. `activating` is the crash loop caught between attempts, and an empty state
        # is systemd answering about a unit it does not know.
        converged = await converge_now(unit_for(tmp_path), FakeSystemctl(state=state))

        assert converged.state == state
        assert converged.is_running is False

    @pytest.mark.parametrize("verb", ["daemon-reload", "enable", "restart", "show"])
    async def test_a_systemctl_refusal_is_raised_rather_than_reported_as_success(
        self, tmp_path: Path, verb: str
    ) -> None:
        # An install that swallowed these would tell the operator the service is running
        # when it is not, which is the one thing this command must never do.
        with pytest.raises(Exception, match="exited 1"):
            await converge_now(unit_for(tmp_path), FakeSystemctl(fails=verb))
