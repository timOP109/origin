"""Versioned, validated JSON project-file persistence."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from direct_infusion_quant.models import AnalysisProject

PROJECT_FORMAT: Final = "direct-infusion-quant-project"
CURRENT_SCHEMA_VERSION: Final = 1


class ProjectFileError(RuntimeError):
    """Base error for project save and reopen failures."""


class UnsupportedProjectVersionError(ProjectFileError):
    """Raised when no safe migration exists for a project schema."""


class ProjectIntegrityError(ProjectFileError):
    """Raised when the stored project payload does not match its fingerprint."""


class InvalidProjectFileError(ProjectFileError):
    """Raised when a project document is malformed or fails validation."""


def save_project(project: AnalysisProject, path: Path) -> None:
    """Validate and atomically save a complete version-one project snapshot."""

    if project.schema_version != CURRENT_SCHEMA_VERSION:
        raise UnsupportedProjectVersionError(
            f"Cannot save project schema {project.schema_version}; "
            f"this application writes schema {CURRENT_SCHEMA_VERSION}."
        )
    project_data = project.model_dump(mode="json")
    document = {
        "format": PROJECT_FORMAT,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "saved_at_utc": datetime.now(UTC).isoformat(),
        "project_sha256": _fingerprint(project_data),
        "project": project_data,
    }
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(document, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ProjectFileError(f"Could not save project to '{destination}'.") from error


def load_project(path: Path) -> AnalysisProject:
    """Reopen and validate a project without changing any stored settings."""

    source = path.expanduser().resolve()
    try:
        with source.open(encoding="utf-8") as project_file:
            document = json.load(project_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InvalidProjectFileError(
            f"Could not read project file '{source}'."
        ) from error

    if not isinstance(document, dict):
        raise InvalidProjectFileError("Project document must be a JSON object.")
    if document.get("format") != PROJECT_FORMAT:
        raise InvalidProjectFileError("File is not a DirectInfusionQuant project.")
    schema_version = document.get("schema_version")
    if schema_version != CURRENT_SCHEMA_VERSION:
        raise UnsupportedProjectVersionError(
            f"Project schema {schema_version!r} is unsupported; "
            f"this application supports schema {CURRENT_SCHEMA_VERSION}."
        )
    project_data = document.get("project")
    if not isinstance(project_data, dict):
        raise InvalidProjectFileError("Project payload must be a JSON object.")
    stored_fingerprint = document.get("project_sha256")
    if not isinstance(stored_fingerprint, str) or not hmac.compare_digest(
        stored_fingerprint, _fingerprint(project_data)
    ):
        raise ProjectIntegrityError(
            "Project payload does not match its saved integrity fingerprint."
        )
    try:
        project = AnalysisProject.model_validate(project_data)
    except ValidationError as error:
        raise InvalidProjectFileError("Project settings failed validation.") from error
    if project.schema_version != schema_version:
        raise InvalidProjectFileError(
            "Document and project payload schema versions do not match."
        )
    return project


def _fingerprint(project_data: dict[str, Any]) -> str:
    canonical = json.dumps(
        project_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
