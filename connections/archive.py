"""Archive extraction utilities with path traversal protection.

This module provides secure extraction for ZIP, TAR, and RAR archives,
preventing path traversal attacks (e.g., files with names like '../../../etc/passwd').
"""

import os
import zipfile
import tarfile
from pathlib import Path
from typing import Union


class PathTraversalError(Exception):
    """Raised when archive contains paths attempting to escape target directory."""
    pass


def _is_safe_path(base: Path, target: Path) -> bool:
    """Check if ``target`` path is safely within ``base`` directory.

    Parameters
    ----------
    base : Path
        Base directory that should contain the target
    target : Path
        Target path to validate

    Returns
    -------
    bool
        True if target is safely within base, False otherwise
    """
    try:
        # Resolve both paths to absolute paths
        base_abs = base.resolve()
        target_abs = target.resolve()

        # Check if target is a child of base
        return base_abs in target_abs.parents or base_abs == target_abs
    except (OSError, ValueError):
        return False


def safe_extract(archive: Union[zipfile.ZipFile, tarfile.TarFile], target_dir: Union[str, Path]) -> None:
    """Safely extract archive contents with path traversal protection.

    Parameters
    ----------
    archive : zipfile.ZipFile | tarfile.TarFile
        Archive object to extract
    target_dir : str | Path
        Target directory for extraction

    Raises
    ------
    PathTraversalError
        If any archive member attempts to escape target directory
    """
    target_path = Path(target_dir).resolve()

    if isinstance(archive, zipfile.ZipFile):
        # ZIP extraction
        for member in archive.namelist():
            # Resolve the full extraction path
            member_path = (target_path / member).resolve()

            # Check for path traversal
            if not _is_safe_path(target_path, member_path):
                raise PathTraversalError(
                    f"Archive member attempts path traversal: {member}"
                )

            # Extract the member
            archive.extract(member, target_path)

    elif isinstance(archive, tarfile.TarFile):
        # TAR extraction
        for member in archive.getmembers():
            # Resolve the full extraction path
            member_path = (target_path / member.name).resolve()

            # Check for path traversal
            if not _is_safe_path(target_path, member_path):
                raise PathTraversalError(
                    f"Archive member attempts path traversal: {member.name}"
                )

            # Extract the member
            archive.extract(member, target_path)
    else:
        raise TypeError(f"Unsupported archive type: {type(archive)}")
