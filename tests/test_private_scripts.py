from __future__ import annotations

from pathlib import Path
import shlex
import stat
import subprocess
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(dedent(content), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_private_script(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        text=True,
    )


def test_release_version_uses_integer_fourth_segment() -> None:
    script = (
        "source private/scripts/_lib.sh\n"
        "release_version_for_counter v2.16.1 4\n"
        "next_release_counter 4\n"
    )

    result = _run_private_script(script)

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["2.16.1.4", "5"]


def test_release_version_rejects_zero_padded_counter() -> None:
    script = "source private/scripts/_lib.sh\nrelease_version_for_counter v2.16.1 004\n"

    result = _run_private_script(script)

    assert result.returncode == 1
    assert "positive integer counter" in result.stderr


def test_private_run_reports_shared_and_fork_homes(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "uv",
        """\
        #!/usr/bin/env bash
        exit 0
        """,
    )
    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "VIBE_RUN_SHOW_HOME": "1",
        "VIBE_HOME": str(tmp_path / "shared"),
        "UVIBE_HOME": str(tmp_path / "fork-state"),
    }

    result = subprocess.run(
        ["bash", "private/run.sh", "--help"],
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        env=env,
        text=True,
    )

    assert result.returncode == 0
    assert f"VIBE_HOME: {tmp_path / 'shared'}" in result.stderr
    assert f"UVIBE_HOME: {tmp_path / 'fork-state'}" in result.stderr


def test_remove_upstream_readme_install_section_preserves_surrounding_content(
    tmp_path: Path,
) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        dedent(
            """\
            # Project

            intro

            ### One-line install (recommended)

            upstream install

            ### Using uv

            uv tool install mistral-vibe

            ## Table of Contents

            - [Usage](#usage)

            ## Usage

            run it
            """
        ),
        encoding="utf-8",
    )

    result = _run_private_script(
        "source private/scripts/_lib.sh\n"
        f"remove_upstream_readme_install_section {shlex.quote(str(tmp_path))}\n"
    )

    assert result.returncode == 0
    rendered = readme.read_text(encoding="utf-8")
    assert "### One-line install (recommended)" not in rendered
    assert "uv tool install mistral-vibe" not in rendered
    assert "# Project" in rendered
    assert "## Table of Contents" in rendered
    assert "## Usage" in rendered


def test_remove_upstream_readme_install_section_fails_on_missing_marker(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text(
        "# Project\n\n## Table of Contents\n", encoding="utf-8"
    )

    result = _run_private_script(
        "source private/scripts/_lib.sh\n"
        f"remove_upstream_readme_install_section {shlex.quote(str(tmp_path))}\n"
    )

    assert result.returncode == 1
    assert "README install section start marker not found" in result.stderr


def test_prepend_fork_readme_copies_images_to_release_root(tmp_path: Path) -> None:
    fake_repo = tmp_path / "fake-repo"
    private_dir = fake_repo / "private"
    asset_dir = private_dir / "assets"
    asset_dir.mkdir(parents=True)
    (asset_dir / "usable-vibe.png").write_bytes(b"image")
    (private_dir / "README.fork").write_text(
        "![Usable Vibe](assets/usable-vibe.png)\n\nFork intro\n", encoding="utf-8"
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("# Upstream\n", encoding="utf-8")

    result = _run_private_script(
        "source private/scripts/_lib.sh\n"
        f"repo_root={shlex.quote(str(fake_repo))}\n"
        f"prepend_fork_readme_once {shlex.quote(str(target))}\n"
    )

    assert result.returncode == 0
    assert (target / "usable-vibe.png").read_bytes() == b"image"
    rendered = (target / "README.md").read_text(encoding="utf-8")
    assert rendered.startswith("![Usable Vibe](usable-vibe.png)\n\nFork intro\n")
    assert "assets/usable-vibe.png" not in rendered


def test_prepend_fork_readme_rejects_unsafe_image_reference(tmp_path: Path) -> None:
    fake_repo = tmp_path / "fake-repo"
    private_dir = fake_repo / "private"
    private_dir.mkdir(parents=True)
    (private_dir / "README.fork").write_text(
        "![Bad](../usable-vibe.png)\n", encoding="utf-8"
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("# Upstream\n", encoding="utf-8")

    result = _run_private_script(
        "source private/scripts/_lib.sh\n"
        f"repo_root={shlex.quote(str(fake_repo))}\n"
        f"prepend_fork_readme_once {shlex.quote(str(target))}\n"
    )

    assert result.returncode == 1
    assert "invalid private README image reference" in result.stderr


def test_prepend_fork_readme_rejects_duplicate_image_basenames(tmp_path: Path) -> None:
    fake_repo = tmp_path / "fake-repo"
    private_dir = fake_repo / "private"
    (private_dir / "assets").mkdir(parents=True)
    (private_dir / "other").mkdir()
    (private_dir / "assets" / "logo.png").write_bytes(b"one")
    (private_dir / "other" / "logo.png").write_bytes(b"two")
    (private_dir / "README.fork").write_text(
        "![One](assets/logo.png)\n![Two](other/logo.png)\n", encoding="utf-8"
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("# Upstream\n", encoding="utf-8")

    result = _run_private_script(
        "source private/scripts/_lib.sh\n"
        f"repo_root={shlex.quote(str(fake_repo))}\n"
        f"prepend_fork_readme_once {shlex.quote(str(target))}\n"
    )

    assert result.returncode == 1
    assert "duplicate private README image basename: logo.png" in result.stderr


def test_rewrite_readme_image_urls_for_pypi_uses_public_root_image_url(
    tmp_path: Path,
) -> None:
    fake_repo = tmp_path / "fake-repo"
    private_dir = fake_repo / "private"
    private_dir.mkdir(parents=True)
    (private_dir / "README.fork").write_text(
        "![Usable Vibe](assets/usable-vibe.png)\n", encoding="utf-8"
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text(
        "![Usable Vibe](usable-vibe.png)\n"
        "[Regular link](usable-vibe.png)\n"
        "![Absolute](https://example.test/logo.png)\n",
        encoding="utf-8",
    )

    result = _run_private_script(
        "source private/scripts/_lib.sh\n"
        "REPO_URL=https://github.com/teeay/usable-vibe\n"
        f"repo_root={shlex.quote(str(fake_repo))}\n"
        f"rewrite_readme_image_urls_for_pypi {shlex.quote(str(target))}\n"
    )

    assert result.returncode == 0
    rendered = (target / "README.md").read_text(encoding="utf-8")
    assert (
        "![Usable Vibe]"
        "(https://github.com/teeay/usable-vibe/raw/main/usable-vibe.png)" in rendered
    )
    assert "[Regular link](usable-vibe.png)" in rendered
    assert "![Absolute](https://example.test/logo.png)" in rendered


def test_release_removes_upstream_install_before_prepending_fork_readme() -> None:
    release_script = (REPO_ROOT / "private" / "scripts" / "release.sh").read_text(
        encoding="utf-8"
    )

    remove_index = release_script.index("remove_upstream_readme_install_section")
    prepend_index = release_script.index("prepend_fork_readme_once")
    assert remove_index < prepend_index


def test_publish_pypi_rewrites_readme_only_for_build() -> None:
    publish_script = (REPO_ROOT / "private" / "scripts" / "publish-pypi.sh").read_text(
        encoding="utf-8"
    )

    backup_index = publish_script.index('cp README.md "${pypi_readme_backup}"')
    rewrite_index = publish_script.index("rewrite_readme_image_urls_for_pypi")
    build_index = publish_script.index("uv build")
    restore_index = publish_script.index("restore_pypi_readme", build_index)
    test_index = publish_script.index("uv run pytest -n auto")
    assert backup_index < rewrite_index < build_index < restore_index < test_index
