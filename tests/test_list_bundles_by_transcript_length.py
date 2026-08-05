"""Tests for listing persisted bundles by transcript character count."""

from pathlib import Path

import pytest

from scripts.list_bundles_by_transcript_length import list_transcript_lengths
from transcriber.constants import TRANSCRIPT_FILENAME


def _create_transcript(store_dir: Path, bundle_name: str, text: str) -> None:
    """Create one bundle transcript using the real temporary filesystem."""
    bundle_dir = store_dir / bundle_name
    bundle_dir.mkdir()
    (bundle_dir / TRANSCRIPT_FILENAME).write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("ascending", "expected_bundles"),
    [
        (False, ["long", "Alpha", "short"]),
        (True, ["short", "Alpha", "long"]),
    ],
)
def test_lists_transcript_character_counts_in_requested_order(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    ascending: bool,
    expected_bundles: list[str],
) -> None:
    """The requested direction controls output while text length counts Unicode characters."""
    _create_transcript(tmp_path, "short", "é")
    _create_transcript(tmp_path, "Alpha", "four")
    _create_transcript(tmp_path, "long", "six\n字")

    without_transcript = list_transcript_lengths(tmp_path, ascending=ascending)

    output_lines = capsys.readouterr().out.splitlines()
    listed_bundles = [line.split(maxsplit=1)[1] for line in output_lines[:3]]
    listed_lengths = [int(line.split(maxsplit=1)[0]) for line in output_lines[:3]]
    expected_lengths = {"short": 1, "Alpha": 4, "long": 5}
    assert without_transcript == 0
    assert listed_bundles == expected_bundles
    assert listed_lengths == [expected_lengths[name] for name in expected_bundles]
    assert output_lines[-1] == "Listed 3 bundle(s); skipped 0 without transcript.md."


def test_skips_non_bundle_entries_and_counts_only_visible_bundles_without_transcripts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Hidden, reserved, and file entries do not affect the missing-transcript count."""
    _create_transcript(tmp_path, "included", "text")
    (tmp_path / "missing").mkdir()
    _create_transcript(tmp_path, ".hidden", "hidden text")
    _create_transcript(tmp_path, "_reserved", "reserved text")
    (tmp_path / "loose-file.txt").write_text("not a bundle", encoding="utf-8")

    without_transcript = list_transcript_lengths(tmp_path, ascending=False)

    output = capsys.readouterr().out
    assert without_transcript == 1
    assert "included" in output
    assert ".hidden" not in output
    assert "_reserved" not in output
    assert "loose-file.txt" not in output
    assert "Listed 1 bundle(s); skipped 1 without transcript.md." in output
