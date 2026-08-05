"""List bundles ordered by transcript character count, longest first.

Usage:
    uv run python scripts/list_bundles_by_transcript_length.py
    uv run python scripts/list_bundles_by_transcript_length.py --ascending
"""

from __future__ import annotations

import argparse
from pathlib import Path

from transcriber.config import TranscribeConfig
from transcriber.constants import TRANSCRIPT_FILENAME


def list_transcript_lengths(store_dir: Path, *, ascending: bool) -> int:
    """Print transcript lengths and return the number of bundles without one."""
    lengths: list[tuple[int, str]] = []
    without_transcript = 0

    for bundle_dir in sorted(store_dir.iterdir(), key=lambda path: path.name.casefold()):
        if not bundle_dir.is_dir() or bundle_dir.name.startswith(("_", ".")):
            continue

        transcript_path = bundle_dir / TRANSCRIPT_FILENAME
        if not transcript_path.is_file():
            without_transcript += 1
            continue

        length = len(transcript_path.read_text(encoding="utf-8"))
        lengths.append((length, bundle_dir.name))

    lengths.sort(key=lambda item: item[0], reverse=not ascending)
    for length, bundle_name in lengths:
        print(f"{length:>10,}  {bundle_name}")

    print(
        f"\nListed {len(lengths)} bundle(s); "
        f"skipped {without_transcript} without {TRANSCRIPT_FILENAME}.",
    )
    return without_transcript


def main() -> None:
    """Run the transcript length listing CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ascending",
        action="store_true",
        help="List shortest transcripts first instead of longest first.",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Bundle store directory. Defaults to general.store_dir.",
    )
    args = parser.parse_args()

    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]
    store_dir = args.store_dir or config.general.store_dir
    list_transcript_lengths(store_dir, ascending=args.ascending)


if __name__ == "__main__":
    main()
