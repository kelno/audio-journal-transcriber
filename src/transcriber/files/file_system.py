"""Abstract file system service for dependency injection."""

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import override

from transcriber.config import TranscribeConfig
from transcriber.constants import DELETED_DIR_NAME


class FileSystemService(ABC):
    """Abstract interface for file system operations."""

    config: TranscribeConfig

    def __init__(self, config: TranscribeConfig):
        self.config = config

    @abstractmethod
    def file_exists(self, path: Path) -> bool:
        """Check if a file exists.

        Args:
            path: Path to the file.

        Returns:
            bool: True if the file exists, False otherwise.

        """
        ...

    @abstractmethod
    def directory_exists(self, path: Path) -> bool:
        """Check if a directory exists.

        Args:
            path: Path to the directory.

        Returns:
            bool: True if the directory exists, False otherwise.

        """
        ...

    @abstractmethod
    def read_file(self, path: Path) -> str:
        """Read file contents.

        Args:
            path: Path to the file to read.

        Returns:
            str: The file contents.

        Raises:
            FileNotFoundError: If the file does not exist.

        """
        ...

    @abstractmethod
    def write_file(self, path: Path, content: str) -> None:
        """Write file contents.

        Args:
            path: Path to the file to write.
            content: Content to write.

        """
        ...

    @abstractmethod
    def delete_file(self, path: Path) -> None:
        """Delete a file.

        Args:
            path: Path to the file to delete.

        Raises:
            FileNotFoundError: If the file does not exist.

        """
        ...

    @abstractmethod
    def delete_directory(self, path: Path) -> None:
        """Delete a file.

        Args:
            path: Path to the file to delete.

        Raises:
            FileNotFoundError: If the file does not exist.

        """
        ...

    @abstractmethod
    def move_file(self, source: Path, destination: Path) -> None:
        """Move or rename a file from source to destination.

        Args:
            source: Path to the existing file.
            destination: Path to the new location.

        Raises:
            FileNotFoundError: If the source file does not exist.

        """
        ...

    @abstractmethod
    def create_directory(self, path: Path) -> None:
        """Create a directory.

        Args:
            path: Path to the directory to create.

        """
        ...

    @abstractmethod
    def rename_directory(self, from_path: Path, to_path: Path) -> None:
        """Rename/move a directory.

        Args:
            from_path: Source directory path.
            to_path: Destination directory path.

        Raises:
            FileNotFoundError: If the source directory does not exist.

        """
        ...

    @abstractmethod
    def list_directory(self, path: Path) -> list[Path]:
        """List all items (files and directories) in a directory (non-recursive).

        Args:
            path: Path to the directory to list.

        Returns:
            list[Path]: List of paths in the directory.

        """
        ...


class RealFileSystemService(FileSystemService):
    """Real file system operations."""

    def _get_backup_path(self, path: Path) -> Path:
        """Get the backup path for a file or directory.

        Args:
            path: Path to the file or directory to backup.

        Returns:
            Path: The backup path where the item should be moved.

        """
        # Always use store_dir as the root for backup structure
        root_dir = self.config.general.store_dir
        deleted_dir = root_dir / DELETED_DIR_NAME

        try:
            # Try to preserve relative structure from store_dir
            relative_path = path.relative_to(root_dir)
            backup_path = deleted_dir / relative_path
        except ValueError:
            # If path is not relative to store_dir, create a flat structure
            # using a unique identifier to avoid collisions
            backup_path = deleted_dir / path.name

            # If the name already exists, append a counter
            counter = 1
            while backup_path.exists():
                backup_path = deleted_dir / f"{path.name}_{counter}"
                counter += 1

        return backup_path

    @override
    def file_exists(self, path: Path) -> bool:
        """Check if a file exists."""
        return path.is_file()

    @override
    def directory_exists(self, path: Path) -> bool:
        """Check if a directory exists."""
        return path.is_dir()

    @override
    def read_file(self, path: Path) -> str:
        """Read file contents."""
        return path.read_text(encoding="utf-8")

    @override
    def write_file(self, path: Path, content: str) -> None:
        """Write file contents."""
        path.write_text(content, encoding="utf-8")

    @override
    def delete_file(self, path: Path) -> None:
        """Delete a file.

        Raises:
            FileNotFoundError: If the file does not exist.

        """
        if not path.exists():
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)

        if self.config.general.safe_delete:
            backup_path = self._get_backup_path(path)

            # Ensure the backup directory exists
            backup_path.parent.mkdir(parents=True, exist_ok=True)

            # Move file to backup location
            path.rename(backup_path)
        else:
            path.unlink()

    @override
    def delete_directory(self, path: Path) -> None:
        """Delete a directory.

        Raises:
            FileNotFoundError: If the directory does not exist.

        """
        if self.config.general.safe_delete:
            backup_path = self._get_backup_path(path)

            # Ensure the backup directory exists
            backup_path.parent.mkdir(parents=True, exist_ok=True)

            # Move directory to backup location
            path.rename(backup_path)
        else:
            shutil.rmtree(path)

    @override
    def move_file(self, source: Path, destination: Path) -> None:
        """Move or rename a file from source to destination."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)

    @override
    def create_directory(self, path: Path) -> None:
        """Create a directory."""
        path.mkdir(parents=True, exist_ok=True)

    @override
    def rename_directory(self, from_path: Path, to_path: Path) -> None:
        """Rename/move a directory."""
        from_path.rename(to_path)

    @override
    def list_directory(self, path: Path) -> list[Path]:
        """List all items (files and directories) in a directory."""
        return list(path.glob("*"))
