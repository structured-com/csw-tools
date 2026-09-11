"""Load project information for the diagnostic version display."""

from __future__ import annotations

import platform
import tomllib
from dataclasses import dataclass
from importlib import metadata as package_metadata
from importlib import resources
from pathlib import Path
from typing import Any

PACKAGE_NAME = "csw-tools"
PYTHON_VERSION_RESOURCE = "project-python-version.txt"


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    """Project fields displayed by ``csw-tools --version``."""

    name: str
    version: str
    description: str
    requires_python: str
    license: str
    homepage: str


def _checkout_root() -> Path | None:
    """Return the project root when running from a source checkout."""

    for directory in Path(__file__).resolve().parents:
        if (directory / "pyproject.toml").is_file():
            return directory
    return None


def _homepage(project_urls: list[str]) -> str:
    for value in project_urls:
        label, separator, url = value.partition(",")
        if separator and label.strip().casefold() == "homepage":
            return url.strip()
    raise RuntimeError("Project metadata does not define a Homepage URL")


def _installed_project_info() -> ProjectInfo:
    project = package_metadata.metadata(PACKAGE_NAME)
    license_value = project.get("License-Expression") or project.get("License")
    if not license_value:
        raise RuntimeError("Project metadata does not define a license")
    return ProjectInfo(
        name=project["Name"],
        version=project["Version"],
        description=project["Summary"],
        requires_python=project["Requires-Python"],
        license=license_value,
        homepage=_homepage(project.get_all("Project-URL", [])),
    )


def _source_project_info(root: Path) -> ProjectInfo:
    with (root / "pyproject.toml").open("rb") as project_file:
        data = tomllib.load(project_file)
    project: dict[str, Any] = data["project"]
    urls: dict[str, str] = project["urls"]
    return ProjectInfo(
        name=project["name"],
        version=project["version"],
        description=project["description"],
        requires_python=project["requires-python"],
        license=project["license"],
        homepage=urls["Homepage"],
    )


def load_project_info() -> ProjectInfo:
    """Load installed metadata, falling back to a source checkout."""

    try:
        return _installed_project_info()
    except package_metadata.PackageNotFoundError:
        root = _checkout_root()
        if root is None:
            raise RuntimeError("Could not locate csw-tools project metadata") from None
        return _source_project_info(root)


def project_python_version() -> str:
    """Return the uv/pyenv project Python target."""

    root = _checkout_root()
    if root is not None:
        value = (root / ".python-version").read_text(encoding="utf-8").strip()
    else:
        value = (
            resources.files("csw_tools")
            .joinpath(PYTHON_VERSION_RESOURCE)
            .read_text(encoding="utf-8")
            .strip()
        )
    if not value:
        raise RuntimeError("The project Python version is empty")
    return " / ".join(value.splitlines())


def format_version_info() -> str:
    """Return the complete user-facing diagnostic version block."""

    project = load_project_info()
    return "\n".join(
        (
            f"{project.name} {project.version}",
            project.description,
            "Python: "
            f"project {project_python_version()} | "
            f"required {project.requires_python} | "
            f"running {platform.python_version()}",
            f"License: {project.license}",
            f"Homepage: {project.homepage}",
        )
    )
