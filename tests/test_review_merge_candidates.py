from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from scripts.review_merge_candidates import (
    NO_TRANSCRIPT_TEXT,
    commit_merge_plan,
    find_candidate_groups,
    format_transcript_preview,
    review_candidate_group,
)
from transcriber.constants import MULTIPLE_TRANSCRIPTS_SEPARATOR
from transcriber.transcribe_bundle import TranscribeBundle

if TYPE_CHECKING:
    from transcriber.config import TranscribeConfig

    from .bundle_fixtures import TranscribeBundleFactory
    from .fake_audio_service import FakeAudioService
    from .fake_file_system import FakeFileSystemService


def _bundle_cache(*bundles: TranscribeBundle) -> dict[str, TranscribeBundle]:
    return {bundle.bundle_id: bundle for bundle in bundles}


def test_candidate_groups_are_transitive(
    fake_config: TranscribeConfig,
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> None:
    """Successive short gaps form one review group even when endpoints are distant."""
    first = transcribe_bundle_factory(
        "first",
        "first.mp3",
        bundle_date=datetime(2025, 1, 15, 9, tzinfo=fake_config.general.timezone),
    )
    second = transcribe_bundle_factory(
        "second",
        "second.mp3",
        bundle_date=datetime(2025, 1, 15, 10, 30, tzinfo=fake_config.general.timezone),
    )
    third = transcribe_bundle_factory(
        "third",
        "third.mp3",
        bundle_date=datetime(2025, 1, 15, 12, tzinfo=fake_config.general.timezone),
    )

    groups = find_candidate_groups(
        [third, first, second],
        max_gap=timedelta(hours=2),
    )

    assert groups == [[first, second, third]]


def test_midnight_connects_distant_bundles_from_same_day(
    fake_config: TranscribeConfig,
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> None:
    """An unknown time connects every recording from its calendar day."""
    unknown_time = transcribe_bundle_factory(
        "unknown-time",
        "unknown.mp3",
        bundle_date=datetime(2025, 1, 15, tzinfo=fake_config.general.timezone),
    )
    morning = transcribe_bundle_factory(
        "morning",
        "morning.mp3",
        bundle_date=datetime(2025, 1, 15, 8, tzinfo=fake_config.general.timezone),
    )
    evening = transcribe_bundle_factory(
        "evening",
        "evening.mp3",
        bundle_date=datetime(2025, 1, 15, 20, tzinfo=fake_config.general.timezone),
    )
    next_day = transcribe_bundle_factory(
        "next-day",
        "next.mp3",
        bundle_date=datetime(2025, 1, 17, 8, tzinfo=fake_config.general.timezone),
    )

    groups = find_candidate_groups(
        [evening, next_day, morning, unknown_time],
        max_gap=timedelta(hours=2),
    )

    assert groups == [[unknown_time, morning, evening]]


def test_exact_two_hour_gap_does_not_create_candidates(
    fake_config: TranscribeConfig,
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> None:
    """The configured gap is strict rather than inclusive."""
    first = transcribe_bundle_factory(
        "first",
        "first.mp3",
        bundle_date=datetime(2025, 1, 15, 9, tzinfo=fake_config.general.timezone),
    )
    second = transcribe_bundle_factory(
        "second",
        "second.mp3",
        bundle_date=datetime(2025, 1, 15, 11, tzinfo=fake_config.general.timezone),
    )

    assert find_candidate_groups([first, second], max_gap=timedelta(hours=2)) == []


def test_preview_identifies_each_bundle_and_preserves_order(
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> None:
    """Preview separators expose stable IDs, titles, order, and missing text."""
    first = transcribe_bundle_factory("first title", "first.mp3", transcript_text="first transcript")
    second = transcribe_bundle_factory("second title", "second.mp3")

    preview = format_transcript_preview([second, first])

    assert preview.index(str(second.bundle_id)) < preview.index(str(first.bundle_id))
    assert "Title: second title" in preview
    assert "Title: first title" in preview
    assert NO_TRANSCRIPT_TEXT in preview
    assert "first transcript" in preview


def test_review_builds_order_and_excludes_candidates(
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> None:
    """Interactive selection can reorder candidates and leave one untouched."""
    first = transcribe_bundle_factory("first", "first.mp3", transcript_text="FIRST TRANSCRIPT BODY")
    second = transcribe_bundle_factory("second", "second.mp3", transcript_text="SECOND TRANSCRIPT BODY")
    third = transcribe_bundle_factory("third", "third.mp3", transcript_text="THIRD TRANSCRIPT BODY")
    responses = iter(("3", "exclude 2", "1", "finish", "yes"))
    output: list[str] = []
    finish_summaries: list[str] = []

    def prompt(message: str) -> str:
        if message.startswith("Commit"):
            # The warning is printed after the compact summary and immediately
            # before this confirmation prompt.
            finish_summaries.append(output[-2])
        return next(responses)

    decision = review_candidate_group(
        [first, second, third],
        prompt=prompt,
        output=output.append,
    )

    assert decision.action == "commit"
    assert decision.ordered_bundles == (third, first)
    assert any("not merged" in message and "second" in message for message in output)
    assert len(finish_summaries) == 1
    finish_summary = finish_summaries[0]
    assert "FINAL MERGE ORDER" in finish_summary
    assert finish_summary.index("third") < finish_summary.index("first") < finish_summary.index("second")
    assert "NOT MERGED" in finish_summary
    assert "THIRD TRANSCRIPT BODY" not in finish_summary
    assert "FIRST TRANSCRIPT BODY" not in finish_summary
    assert "SECOND TRANSCRIPT BODY" not in finish_summary


def test_commit_merge_plan_retains_first_and_follows_transcript_order(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
    fake_audio_service: FakeAudioService,
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> None:
    """A committed plan retains its first bundle and appends in reviewed order."""
    first = transcribe_bundle_factory("first", "first.mp3", transcript_text="first transcript")
    second = transcribe_bundle_factory("second", "second.mp3", transcript_text="second transcript")
    third = transcribe_bundle_factory("third", "third.mp3", transcript_text="third transcript")
    bundle_cache = _bundle_cache(first, second, third)

    survivor = commit_merge_plan([third, first, second], bundle_cache, output=lambda _message: None)

    assert survivor is third
    assert set(bundle_cache) == {third.bundle_id}
    reloaded = TranscribeBundle.from_existing_directory(
        third.get_bundle_dir(),
        fake_config,
        fake_fs,
        fake_audio_service,
    )
    assert reloaded.transcript is not None
    assert reloaded.transcript.text == MULTIPLE_TRANSCRIPTS_SEPARATOR.join(
        ("third transcript", "first transcript", "second transcript"),
    )
