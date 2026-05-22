"""Fake in-memory file system service for testing."""

from pathlib import Path

from transcriber.file_system import FileSystemService


class FakeFileSystemService(FileSystemService):
    """In-memory fake file system for testing.

    Tracks all operations for verification in tests.
    """

    def __init__(self) -> None:
        """Initialize the fake file system."""
        self.files: dict[Path, str] = {}
        self.directories: set[Path] = set()
        self.operations: list[tuple[str, Path]] = []

    def file_exists(self, path: Path) -> bool:
        """Check if a file exists."""
        return path in self.files

    def directory_exists(self, path: Path) -> bool:
        """Check if a directory exists."""
        return path in self.directories

    def read_file(self, path: Path) -> str:
        """Read file contents.

        Raises:
            FileNotFoundError: If the file does not exist.

        """
        if path not in self.files:
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)
        self.operations.append(("read", path))
        return self.files[path]

    def write_file(self, path: Path, content: str) -> None:
        """Write file contents."""
        # Ensure parent directory exists
        parent = path.parent
        if parent != Path(".") and not self.directory_exists(parent):
            self.create_directory(parent)
        self.files[path] = content
        self.operations.append(("write", path))

    def delete_file(self, path: Path) -> None:
        """Delete a file.

        Raises:
            FileNotFoundError: If the file does not exist.

        """
        if path not in self.files:
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)
        del self.files[path]
        self.operations.append(("delete", path))

    def create_directory(self, path: Path) -> None:
        """Create a directory."""
        # Don't create root or current directory
        if path == Path(".") or str(path) == "":
            return

        self.directories.add(path)
        # Also create parent directories
        parent = path.parent
        if parent != path and parent != Path("."):  # Avoid infinite recursion
            self.create_directory(parent)
        self.operations.append(("mkdir", path))

    def rename_directory(self, from_path: Path, to_path: Path) -> None:
        """Rename/move a directory.

        Raises:
            FileNotFoundError: If the source directory does not exist.

        """
        if from_path not in self.directories:
            msg = f"Directory not found: {from_path}"
            raise FileNotFoundError(msg)

        # Move the directory itself
        self.directories.remove(from_path)
        self.directories.add(to_path)

        # Move all files under this directory
        files_to_move = {
            path: content
            for path, content in self.files.items()
            if str(path).startswith(str(from_path))
        }
        for old_path, content in files_to_move.items():
            new_path = Path(str(old_path).replace(str(from_path), str(to_path), 1))
            del self.files[old_path]
            self.files[new_path] = content

        # Move all subdirectories
        dirs_to_move = [
            d for d in self.directories if str(d).startswith(str(from_path))
        ]
        for old_dir in dirs_to_move:
            self.directories.remove(old_dir)
            new_dir = Path(str(old_dir).replace(str(from_path), str(to_path), 1))
            self.directories.add(new_dir)

        self.operations.append(("rename", from_path))

    def list_directory(self, path: Path) -> list[Path]:
        """List all items (files and directories) in a directory."""
        items = []

        # Add files directly in this directory
        for file_path in self.files:
            if file_path.parent == path:
                items.append(file_path)

        # Add directories directly in this directory
        for dir_path in self.directories:
            if dir_path.parent == path:
                items.append(dir_path)

        return items

    def clear(self) -> None:
        """Clear all state (useful for test isolation)."""
        self.files.clear()
        self.directories.clear()
        self.operations.clear()

    def get_operations(
        self, operation_type: str | None = None
    ) -> list[tuple[str, Path]]:
        """Get all operations, optionally filtered by type.

        Args:
            operation_type: Optional operation type to filter by (e.g., "write", "read")

        Returns:
            List of (operation_type, path) tuples.

        """
        if operation_type is None:
            return self.operations
        return [op for op in self.operations if op[0] == operation_type]
