from __future__ import annotations

import os
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


def _fork_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (
        (REPO_ROOT / "private" / "fork.env").read_text(encoding="utf-8").splitlines()
    ):
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        parsed = shlex.split(raw_value)
        values[key] = parsed[0] if parsed else ""
    return values


def test_release_version_uses_integer_fourth_segment() -> None:
    script = (
        "source private/scripts/_lib.sh\n"
        "release_version_for_counter v2.19.0 11\n"
        "next_release_counter 11\n"
    )

    result = _run_private_script(script)

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["2.19.0.11", "12"]


def test_release_version_rejects_zero_padded_counter() -> None:
    script = "source private/scripts/_lib.sh\nrelease_version_for_counter v2.19.0 005\n"

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


def test_patch_pyproject_adds_release_project_icon_url(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        dedent(
            """\
            [project]
            name = "mistral-vibe"
            version = "2.19.0"
            description = "Minimal CLI coding agent by Mistral"
            authors = [{ name = "Mistral AI" }]
            keywords = ["ai", "mistral", "developer-tools"]

            [project.urls]
            Homepage = "https://github.com/mistralai/mistral-vibe"

            [project.scripts]
            vibe = "vibe.cli.entrypoint:main"
            vibe-acp = "vibe.acp.entrypoint:main"
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO_ROOT),
            "--directory",
            str(tmp_path),
            "python",
            str(REPO_ROOT / "private/scripts/patch_pyproject.py"),
        ],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env={
            **os.environ,
            "DIST_NAME": "uvibe",
            "SCRIPT_NAME": "uvibe",
            "DISPLAY_NAME": "Usable Vibe",
            "AUTHOR_NAME": "Mistral AI, edited by TeeAy",
            "REPO_URL": "https://github.com/teeay/usable-vibe",
            "DOCS_URL": "https://teeay.dev/oss/uvibe",
            "ICON_URL": "https://teeay.dev/images/oss/usable-vibe-icon.png",
            "UPSTREAM_DISPLAY": "Mistral Vibe",
            "FORK_VERSION": "2.19.0.11",
        },
        text=True,
    )

    assert result.returncode == 0
    rendered = pyproject.read_text(encoding="utf-8")
    assert 'name = "uvibe"' in rendered
    assert 'Icon = "https://teeay.dev/images/oss/usable-vibe-icon.png"' in rendered


def test_set_release_version_accepts_acp_initialize_version_binding(
    tmp_path: Path,
) -> None:
    (tmp_path / "vibe").mkdir()
    (tmp_path / "tests" / "acp").mkdir(parents=True)
    (tmp_path / "distribution" / "zed").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """\
            [project]
            name = "uvibe"
            version = "2.19.0"
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "vibe" / "__init__.py").write_text(
        '__version__ = "2.19.0"\n', encoding="utf-8"
    )
    (tmp_path / "tests" / "acp" / "test_initialize.py").write_text(
        "from vibe import __version__\n"
        "agent_info = Implementation(version=__version__)\n",
        encoding="utf-8",
    )
    (tmp_path / "distribution" / "zed" / "extension.toml").write_text(
        dedent(
            """\
            version = "2.19.0"
            archive = "https://example.test/releases/download/v2.19.0/vibe-acp-darwin-aarch64-2.19.0.zip"
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO_ROOT),
            "python",
            str(REPO_ROOT / "private/scripts/set_release_version.py"),
            "2.19.0.11",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert 'version = "2.19.0.11"' in (tmp_path / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert (tmp_path / "vibe" / "__init__.py").read_text(encoding="utf-8") == (
        '__version__ = "2.19.0.11"\n'
    )
    assert "version=__version__" in (
        tmp_path / "tests" / "acp" / "test_initialize.py"
    ).read_text(encoding="utf-8")
    zed_extension = (tmp_path / "distribution" / "zed" / "extension.toml").read_text(
        encoding="utf-8"
    )
    assert 'version = "2.19.0.11"' in zed_extension
    assert "releases/download/v2.19.0.11" in zed_extension
    assert "-2.19.0.11.zip" in zed_extension


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


def test_release_pushes_internal_commits_and_tag_before_external_publish() -> None:
    release_script = (REPO_ROOT / "private" / "scripts" / "release.sh").read_text(
        encoding="utf-8"
    )

    master_commit_index = release_script.index(
        'uv run git commit -m "release: ${DISPLAY_NAME} ${release_version}"'
    )
    tag_index = release_script.index(
        'uv run git tag -a "${release_tag}" -m "${DISPLAY_NAME} ${release_version}"'
    )
    patch_commit_index = release_script.index(
        'uv run git commit -m "release: prepare ${next_counter}"'
    )
    push_patches_index = release_script.index("uv run git push origin patches")
    push_master_index = release_script.index(
        'uv run git -C "${master_dir}" push origin master'
    )
    push_tag_index = release_script.index(
        'uv run git -C "${master_dir}" push origin "${release_tag}"'
    )
    external_index = release_script.index("publish-external.sh")

    assert (
        master_commit_index
        < tag_index
        < patch_commit_index
        < push_patches_index
        < push_master_index
        < push_tag_index
        < external_index
    )


def test_rebrand_covers_e2e_local_spawn_and_display_literals() -> None:
    rebrand_script = (REPO_ROOT / "private" / "scripts" / "rebrand.sh").read_text(
        encoding="utf-8"
    )
    fork_env = _fork_env()
    script_name = fork_env["SCRIPT_NAME"]
    display_name = fork_env["DISPLAY_NAME"]
    upstream_display = fork_env["UPSTREAM_DISPLAY"]
    required_command_literals = {
        "tests/e2e/test_cli_native_scroll.py": (
            '["run", "vibe", "--workdir", str(e2e_workdir)]',
            f'["run", "{script_name}", "--workdir", str(e2e_workdir)]',
        ),
        "tests/e2e/test_tmux_reflow.py": (
            "uv run vibe --workdir",
            f"uv run {script_name} --workdir",
        ),
    }

    for relative_path, (
        upstream_command_literal,
        released_command_literal,
    ) in required_command_literals.items():
        source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert upstream_display in source or display_name in source
        assert upstream_command_literal in source or released_command_literal in source
        if upstream_display in source or upstream_command_literal in source:
            assert relative_path in rebrand_script
        if upstream_command_literal in source:
            assert upstream_command_literal in rebrand_script


def test_rebrand_updates_all_e2e_resume_hint_regexes() -> None:
    rebrand_script = (REPO_ROOT / "private" / "scripts" / "rebrand.sh").read_text(
        encoding="utf-8"
    )
    script_name = _fork_env()["SCRIPT_NAME"]
    upstream_regex = 'r"Or: vibe --resume ([0-9a-f-]+)"'
    released_regex = f'r"Or: {script_name} --resume ([0-9a-f-]+)"'
    files_with_resume_hint: list[Path] = []

    for path in (REPO_ROOT / "tests" / "e2e").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if "Or:" not in content or "--resume ([0-9a-f-]+)" not in content:
            continue

        files_with_resume_hint.append(path)
        if upstream_regex in content:
            assert path.relative_to(REPO_ROOT).as_posix() in rebrand_script
        else:
            assert released_regex in content

    assert files_with_resume_hint


def test_rebrand_rewrites_existing_prompt_markdown_dynamically() -> None:
    rebrand_script = (REPO_ROOT / "private" / "scripts" / "rebrand.sh").read_text(
        encoding="utf-8"
    )

    assert "find vibe/core/prompts -maxdepth 1 -type f -name '*.md'" in rebrand_script
    assert "cli_2026-06_emoji.md" not in rebrand_script


def test_rebrand_covers_upstream_skill_command_literals() -> None:
    rebrand_script = (REPO_ROOT / "private" / "scripts" / "rebrand.sh").read_text(
        encoding="utf-8"
    )
    skill_path = ".vibe/skills/instrument-feature-analytics/SKILL.md"
    skill_source = (REPO_ROOT / skill_path).read_text(encoding="utf-8")
    script_name = _fork_env()["SCRIPT_NAME"]

    assert "uv run vibe" in skill_source or f"uv run {script_name}" in skill_source
    if "uv run vibe" in skill_source:
        assert skill_path in rebrand_script
        assert "'uv run vibe'" in rebrand_script
        assert '"uv run ${SCRIPT_NAME}"' in rebrand_script


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


def test_publish_pypi_help_describes_prompted_publish_flow() -> None:
    result = subprocess.run(
        ["bash", "private/scripts/publish-pypi.sh", "--help"],
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        text=True,
    )

    assert result.returncode == 0
    assert "Default mode builds and tests the package" in result.stdout
    assert "then asks" in result.stdout
    assert "prompts for the PyPI token" in result.stdout


def test_publish_pypi_default_flow_asks_to_publish_after_validation() -> None:
    publish_script = (REPO_ROOT / "private" / "scripts" / "publish-pypi.sh").read_text(
        encoding="utf-8"
    )

    test_index = publish_script.index("uv run pytest -n auto")
    validation_index = publish_script.index("write_validation", test_index)
    confirm_index = publish_script.index("if confirm_publish", validation_index)
    prompt_index = publish_script.index("prompt_for_token_if_needed", confirm_index)
    publish_index = publish_script.index("publish_artifacts", prompt_index)
    assert test_index < validation_index < confirm_index < prompt_index < publish_index


def test_publish_pypi_existing_artifact_flow_prompts_before_upload() -> None:
    publish_script = (REPO_ROOT / "private" / "scripts" / "publish-pypi.sh").read_text(
        encoding="utf-8"
    )

    branch_index = publish_script.index('if [[ "${publish}" == 1 ]]; then')
    validation_index = publish_script.index("require_validated_artifacts", branch_index)
    prompt_index = publish_script.index("prompt_for_token_if_needed", validation_index)
    publish_index = publish_script.index("publish_artifacts", prompt_index)
    default_branch_index = publish_script.index("else", branch_index)
    assert (
        branch_index
        < validation_index
        < prompt_index
        < publish_index
        < default_branch_index
    )


def test_publish_external_commits_tags_and_pushes_release() -> None:
    publish_script = (
        REPO_ROOT / "private" / "scripts" / "publish-external.sh"
    ).read_text(encoding="utf-8")

    assert "--push" not in publish_script
    assert "--message" not in publish_script
    assert "prompt_commit_message()" in publish_script
    assert "External repository commit message" in publish_script
    assert "external commit message required" in publish_script
    assert (
        "printf '\\nExternal repository commit message [%s]: ' "
        '"${default_commit_message}" >/dev/tty'
    ) in publish_script
    assert "read -r answer </dev/tty" in publish_script
    tag_guard_index = publish_script.index("refs/tags/${release_tag}")
    sync_index = publish_script.index("rsync -a --delete")
    add_index = publish_script.index('uv run git -C "${external_dir}" add -A')
    prompt_index = publish_script.index('commit_message="$(prompt_commit_message)"')
    commit_index = publish_script.index('uv run git -C "${external_dir}" commit')
    tag_index = publish_script.index('uv run git -C "${external_dir}" tag -a')
    push_branch_index = publish_script.index(
        'uv run git -C "${external_dir}" push origin "${external_branch}"'
    )
    push_tag_index = publish_script.index(
        'uv run git -C "${external_dir}" push origin "${release_tag}"'
    )
    assert (
        tag_guard_index
        < sync_index
        < add_index
        < prompt_index
        < commit_index
        < tag_index
        < push_branch_index
        < push_tag_index
    )
