from __future__ import annotations

import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tomllib

import pexpect
import pytest

from tests import TESTS_ROOT
from tests.e2e.common import (
    ansi_tolerant_pattern,
    send_ctrl_c_until_quit_confirmation,
    wait_for_main_screen,
    wait_for_request_count,
)
from tests.e2e.conftest import restore_editable_native
from tests.e2e.mock_server import StreamingMockServer

_WHEEL_BUILD_TIMEOUT_SECONDS = 270
_WHEEL_INSTALL_TIMEOUT_SECONDS = 60


def _venv_executable(venv_path: Path, name: str) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / f"{name}.exe"
    return venv_path / "bin" / name


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        proc.wait(timeout=5)


def _built_wheels(directory: Path) -> list[Path]:
    return sorted(directory.glob("uvibe-*.whl"))


def _build_wheel(dist_dir: Path) -> Path:
    repo = TESTS_ROOT.parent
    version = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    # Release already built dist/; a second uv build re-resolves maturin on PyPI.
    existing = [
        wheel for wheel in _built_wheels(repo / "dist") if f"-{version}-" in wheel.name
    ]
    if existing:
        return existing[-1]

    command = ["uv", "build", "--wheel", "--out-dir", str(dist_dir)]
    output = b""
    returncode = 1
    for _attempt in range(2):
        proc = subprocess.Popen(
            command,
            cwd=repo,
            start_new_session=os.name != "nt",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            output, _ = proc.communicate(timeout=_WHEEL_BUILD_TIMEOUT_SECONDS)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            _terminate_process_group(proc)
            raise
        finally:
            restore_editable_native()
        if returncode == 0:
            break
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, command, output=output)
    wheels = _built_wheels(dist_dir)
    assert len(wheels) == 1
    return wheels[0]


def _install_fresh_wheel(tmp_path: Path, wheel_path: Path) -> Path:
    venv_path = tmp_path / "fresh-install-venv"
    repo = TESTS_ROOT.parent
    reqs = tmp_path / "wheel-deps.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--all-extras",
            "--no-emit-project",
            "--no-dev",
            "-o",
            str(reqs),
        ],
        cwd=repo,
        check=True,
        timeout=_WHEEL_INSTALL_TIMEOUT_SECONDS,
    )
    subprocess.run(
        ["uv", "venv", "--no-config", "--python", sys.executable, str(venv_path)],
        cwd=tmp_path,
        check=True,
    )
    python_path = _venv_executable(venv_path, "python")

    def reqs_command(*flags: str) -> list[str]:
        return [
            "uv",
            "pip",
            "install",
            *flags,
            "--no-config",
            "--python",
            str(python_path),
            "-r",
            str(reqs),
        ]

    # Hashed lockfile export installs from the uv cache. A registry fetch
    # during the parallel suite can hang until pytest-timeout.
    synced = subprocess.run(
        reqs_command("--offline"),
        cwd=tmp_path,
        capture_output=True,
        timeout=_WHEEL_INSTALL_TIMEOUT_SECONDS,
    )
    if synced.returncode != 0:
        subprocess.run(
            reqs_command(),
            cwd=tmp_path,
            check=True,
            timeout=_WHEEL_INSTALL_TIMEOUT_SECONDS,
            env={**os.environ, "UV_HTTP_TIMEOUT": "15"},
        )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-config",
            "--no-deps",
            "--force-reinstall",
            "--python",
            str(python_path),
            str(wheel_path),
        ],
        cwd=tmp_path,
        check=True,
        timeout=_WHEEL_INSTALL_TIMEOUT_SECONDS,
    )
    return _venv_executable(venv_path, "uvibe")


@pytest.mark.timeout(300)
def test_fresh_wheel_install_can_spawn_cli_and_complete_happy_path(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel_path = _build_wheel(tmp_path / "dist")
    vibe_executable = _install_fresh_wheel(tmp_path, wheel_path)

    monkeypatch.delenv("PYTHONPATH", raising=False)
    vibe_home = Path(os.environ["VIBE_HOME"])
    uvibe_home = Path(os.environ["UVIBE_HOME"])
    (vibe_home / "cache.toml").write_text(
        '[update_cache]\nlatest_version = "9.9.9"\nstored_at_timestamp = 1800000000\n',
        encoding="utf-8",
    )

    captured = io.StringIO()
    child = pexpect.spawn(
        str(vibe_executable),
        ["--workdir", str(e2e_workdir)],
        cwd=str(tmp_path),
        env=os.environ,
        encoding="utf-8",
        timeout=30,
        dimensions=(36, 120),
    )
    child.logfile_read = captured

    try:
        wait_for_main_screen(child, timeout=20)
        child.send("Greet")
        child.send("\r")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests),
            expected_count=1,
            timeout=10,
            child=child,
        )
        child.expect(ansi_tolerant_pattern("Hello from mock server"), timeout=10)

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)
    finally:
        if child.isalive():
            child.terminate(force=True)
        if not child.closed:
            child.close()

    output = captured.getvalue()
    assert "Welcome to Usable Vibe" not in output
    assert "A new Vibe release is available" not in output
    if (uvibe_home / "cache.toml").exists():
        with (uvibe_home / "cache.toml").open("rb") as f:
            fork_cache = tomllib.load(f)
        assert fork_cache.get("update_cache", {}).get("latest_version") != "9.9.9"
    assert streaming_mock_server.requests[-1].get("model") == "mock-model"
