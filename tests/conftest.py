"""Pytest fixtures for the transcribe tests.

per doc:
    > The conftest.py file serves as a means of providing fixtures for an entire directory
"""

from .fake_config import fake_config  # noqa: F401
from .fake_file_system import FakeFileSystemService, fake_fs  # noqa: F401
