from __future__ import annotations

from email.message import Message
from importlib import resources
from pathlib import Path

import pytest

import csw_tools.project_info as project_info


def test_installed_project_metadata_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = Message()
    metadata["Name"] = "csw-tools"
    metadata["Version"] = "9.8.7"
    metadata["Summary"] = "Test description"
    metadata["Requires-Python"] = ">=3.12"
    metadata["License-Expression"] = "Apache-2.0"
    metadata["Project-URL"] = "Homepage, https://example.test/csw-tools"
    metadata["Project-URL"] = "Issues, https://example.test/issues"
    monkeypatch.setattr(
        project_info.package_metadata, "metadata", lambda _name: metadata
    )

    loaded = project_info.load_project_info()

    assert loaded == project_info.ProjectInfo(
        name="csw-tools",
        version="9.8.7",
        description="Test description",
        requires_python=">=3.12",
        license="Apache-2.0",
        homepage="https://example.test/csw-tools",
    )


def test_source_checkout_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "csw-tools"
version = "1.2.3"
description = "Source checkout"
requires-python = ">=3.12"
license = "Apache-2.0"

[project.urls]
Homepage = "https://example.test/source"
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / ".python-version").write_text("3.13\n", encoding="utf-8")

    def not_installed(_name: str) -> Message:
        raise project_info.package_metadata.PackageNotFoundError

    monkeypatch.setattr(project_info.package_metadata, "metadata", not_installed)
    monkeypatch.setattr(project_info, "_checkout_root", lambda: tmp_path)

    loaded = project_info.load_project_info()

    assert loaded.version == "1.2.3"
    assert loaded.description == "Source checkout"
    assert loaded.homepage == "https://example.test/source"
    assert project_info.project_python_version() == "3.13"


def test_packaged_python_version_matches_project_file() -> None:
    project_version = Path(".python-version").read_text(encoding="utf-8").strip()
    packaged_version = (
        resources.files("csw_tools")
        .joinpath(project_info.PYTHON_VERSION_RESOURCE)
        .read_text(encoding="utf-8")
        .strip()
    )

    assert packaged_version == project_version


def test_packaged_python_version_is_used_outside_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(project_info, "_checkout_root", lambda: None)

    assert project_info.project_python_version() == "3.12"
