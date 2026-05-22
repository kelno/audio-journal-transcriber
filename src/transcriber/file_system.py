"""Abstract file system service for dependency injection."""

from abc import ABC, abstractmethod
from pathlib import Path


class FileSystemService(ABC):
    """Abstract interface for file system operations."""

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

    def file_exists(self, path: Path) -> bool:
        """Check if a file exists."""
        return path.is_file()

    def directory_exists(self, path: Path) -> bool:
        """Check if a directory exists."""
        return path.is_dir()

    def read_file(self, path: Path) -> str:
        """Read file contents."""
        return path.read_text(encoding="utf-8")

    def write_file(self, path: Path, content: str) -> None:
        """Write file contents."""
        path.write_text(content, encoding="utf-8")

    def delete_file(self, path: Path) -> None:
        """Delete a file."""
        path.unlink()

    def create_directory(self, path: Path) -> None:
        """Create a directory."""
        path.mkdir(parents=True, exist_ok=True)

    def rename_directory(self, from_path: Path, to_path: Path) -> None:
        """Rename/move a directory."""
        from_path.rename(to_path)

    def list_directory(self, path: Path) -> list[Path]:
        """List all items (files and directories) in a directory."""
        return list(path.glob("*"))
