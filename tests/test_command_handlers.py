from datetime import datetime
from pathlib import Path

import pytest

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_audio_service import FakeAudioService
from tests.fake_file_system import FakeFileSystemService
from transcriber.actions.action import MergeAction, PreviousBundleTarget, SetTitleAction
from transcriber.actions.action_executors import BundleActionExecutor
from transcriber.actions.action_request import ActionFailed, ActionResult
from transcriber.bundle_title import BundleTitleState
from transcriber.commands.command import Command
from transcriber.commands.command_handlers import merge_bundles
from transcriber.config import TranscribeConfig
from transcriber.constants import MERGE_FAILED_FILENAME, MULTIPLE_TRANSCRIPTS_SEPARATOR
from transcriber.exception import MergeBlockedException
from transcriber.transcribe_bundle import TranscribeBundle


def _bundle_cache(*bundles: TranscribeBundle) -> dict[str, TranscribeBundle]:
    """Index test bundles by their persistent IDs.

    Args:
        *bundles: Bundle instances to include in the test cache.

    Returns:
        A cache keyed by each bundle's persistent ID.

    """
    return {bundle.bundle_id: bundle for bundle in bundles}


def _execute_previous_merge(
    source: TranscribeBundle,
    _config: TranscribeConfig,
    bundle_cache: dict[str, TranscribeBundle],
    _command: Command,
) -> ActionResult:
    """Exercise the production action boundary used by a voice merge command."""
    return BundleActionExecutor(bundle_cache).execute(
        MergeAction(
            source_bundle_id=source.bundle_id,
            target=PreviousBundleTarget(),
        ),
    )


class TestMergeActionExecutor:
    def test_merge_bundles_uses_explicit_target_without_a_command(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """A reviewed merge can target any distinct bundle and needs no fake command."""
        source = transcribe_bundle_factory(
            bundle_name="2025-01-15_older-source",
            audio_filename="Recording 20250115090000.mp3",
            bundle_date=datetime(2025, 1, 15, 9, tzinfo=fake_config.general.timezone),
            transcript_text="source transcript",
            commands=["leave this unexecuted"],
        )
        target = transcribe_bundle_factory(
            bundle_name="2025-01-15_newer-target",
            audio_filename="Recording 20250115100000.mp3",
            bundle_date=datetime(2025, 1, 15, 10, tzinfo=fake_config.general.timezone),
            transcript_text="target transcript",
            summary_text="stale target summary",
        )
        target.metadata.bundle_title_state = BundleTitleState.GENERATED
        target.metadata.write(target.get_bundle_dir(), fake_fs)
        bundle_cache = _bundle_cache(source, target)

        merged_target = merge_bundles(source, target, bundle_cache)

        assert merged_target is target
        assert source.bundle_id not in bundle_cache
        assert target.bundle_id in bundle_cache
        assert not fake_fs.directory_exists(source.get_bundle_dir())

        reloaded = TranscribeBundle.from_existing_directory(
            existing_dir=target.get_bundle_dir(),
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        assert reloaded.bundle_id == target.bundle_id
        assert reloaded.transcript is not None
        assert reloaded.transcript.text == ("target transcript" + MULTIPLE_TRANSCRIPTS_SEPARATOR + "source transcript")
        assert reloaded.summary is None
        assert reloaded.metadata.bundle_title_state is BundleTitleState.PENDING
        assert reloaded.commands is not None
        assert reloaded.commands.commands[0].executed is False

    def test_merge_dedupes_commands_with_same_id(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify commands sharing a stable id collapse to one on merge (copy/paste scenario)."""
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
            commands=["shared", "only previous"],
        )
        previous_bundle.metadata.bundle_title_state = BundleTitleState.GENERATED
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            commands=["shared", "only current"],
        )

        bundle_cache = _bundle_cache(current_bundle, previous_bundle)
        # Simulate a copy/pasted bundle: the "shared" command carries the same id
        # in both bundles, while the distinct commands keep their own ids.
        assert previous_bundle.commands
        assert current_bundle.commands
        shared_cmd = previous_bundle.commands.commands[0]
        shared_id = shared_cmd.id
        current_bundle.commands.commands[0].id = shared_id
        previous_bundle.commands.write(previous_bundle.get_bundle_dir(), fake_fs)
        current_bundle.commands.write(current_bundle.get_bundle_dir(), fake_fs)

        previous_bundle_dir = previous_bundle.get_bundle_dir()
        current_bundle_dir = current_bundle.get_bundle_dir()
        target_id = previous_bundle.bundle_id
        source_id = current_bundle.bundle_id

        _execute_previous_merge(current_bundle, fake_config, bundle_cache, shared_cmd)

        assert not fake_fs.directory_exists(current_bundle_dir)
        assert target_id in bundle_cache
        assert source_id not in bundle_cache

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # The shared id appears once; the two distinct commands are both kept.
        assert merged_bundle.commands is not None
        commands = merged_bundle.commands.commands
        assert len(commands) == 3
        assert merged_bundle.bundle_id == target_id
        assert [cmd.text for cmd in commands] == ["shared", "only previous", "only current"]
        assert sum(1 for cmd in commands if cmd.id == shared_id) == 1

    def test_merge_merges_with_recent_previous_bundle(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify merge can join bundles while preserving all command state and other metadata."""
        previous_audio_filename = "Recording 20250115010000.mp3"
        previous_cmd_raw1 = "keep this"
        previous_cmd_raw2 = "execute me too"
        previous_transcript_text = "previous transcript"
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename=previous_audio_filename,
            transcript_text="previous transcript",
            summary_text="previous summary",
            commands=[previous_cmd_raw1, previous_cmd_raw2],
            commands_executed=False,
            transcript_model_used="model-a",
            keep_forever=True,
        )
        # Manually mark only first command as executed to test mixed states
        assert previous_bundle.commands
        previous_bundle.commands.commands[0].executed = True
        previous_bundle_dir = previous_bundle.get_bundle_dir()
        previous_bundle.commands.write(previous_bundle_dir, fake_fs)

        current_audio_filename = "Recording 20250115090000.mp3"
        current_command_raw1 = "merge this"
        current_command_raw2 = "blah blah"
        current_command_raw3 = "final command"
        current_transcript_text = "current transcript"
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename=current_audio_filename,
            transcript_text=current_transcript_text,
            commands=[current_command_raw1, current_command_raw2, current_command_raw3],
            commands_executed=False,
            transcript_model_used="model-b",
            keep_forever=False,
        )
        # Mark only second command as executed
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)
        merge_cmd = current_bundle.commands.commands[0]
        assert merge_cmd.executed is False  # our merge command, starts at false
        current_bundle.commands.commands[1].executed = True
        current_bundle_dir = current_bundle.get_bundle_dir()
        current_bundle.commands.write(current_bundle_dir, fake_fs)

        _execute_previous_merge(current_bundle, fake_config, bundle_cache, merge_cmd)

        assert not fake_fs.directory_exists(current_bundle_dir)

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Verify audio files were merged
        assert {path.name for path in merged_bundle.source_audios} == {
            previous_audio_filename,
            current_audio_filename,
        }

        # Verify transcripts were concatenated
        assert merged_bundle.transcript is not None
        assert previous_transcript_text in merged_bundle.transcript.text
        assert current_transcript_text in merged_bundle.transcript.text
        assert MULTIPLE_TRANSCRIPTS_SEPARATOR in merged_bundle.transcript.text

        # Verify summary was cleared
        assert merged_bundle.summary is None
        assert merged_bundle.metadata.bundle_title_state is BundleTitleState.PENDING

        # Verify all commands were preserved with their execution states
        assert merged_bundle.commands is not None
        commands = merged_bundle.commands.commands
        assert len(commands) == 5
        assert [cmd.text for cmd in commands] == [
            previous_cmd_raw1,
            previous_cmd_raw2,
            current_command_raw1,
            current_command_raw2,
            current_command_raw3,
        ]

        # Verify execution states were preserved from source bundles
        assert commands[0].executed is True  # previous bundle first command
        assert commands[1].executed is False  # previous bundle second command
        # The executor preserves the unresolved merge command; the request
        # lifecycle writes its terminal receipt after mutation succeeds.
        assert commands[2].executed is False
        assert commands[3].executed is True  # current bundle second command
        assert commands[4].executed is False  # current bundle third command, never executed, should still be false

        # Verify transcript model usage was merged (both models present across files)
        merged_models = [model for audio_meta in merged_bundle.metadata.audio_files for model in audio_meta.transcript_model_used]
        assert "model-a" in merged_models
        assert "model-b" in merged_models

        # Verify keep_forever is promoted to True when either source bundle is kept forever
        assert merged_bundle.metadata.keep_forever is True

    def test_merge_preserves_audio_metadata_when_filenames_collide(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Each same-named audio keeps its own metadata after collision renaming."""
        audio_filename = "Recording 20250115090000.mp3"
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename=audio_filename,
            bundle_date=datetime(2025, 1, 15, 8, tzinfo=fake_config.general.timezone),
            transcript_model_used="target-model",
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_current",
            audio_filename=audio_filename,
            bundle_date=datetime(2025, 1, 15, 9, tzinfo=fake_config.general.timezone),
            transcript_model_used="source-model",
            commands=["merge"],
        )
        assert current_bundle.commands is not None

        _execute_previous_merge(
            current_bundle,
            fake_config,
            _bundle_cache(previous_bundle, current_bundle),
            current_bundle.commands.commands[0],
        )

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle.get_bundle_dir(),
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        metadata_by_filename = {audio_meta.filename: audio_meta.transcript_model_used for audio_meta in merged_bundle.metadata.audio_files}
        assert metadata_by_filename == {
            audio_filename: ["target-model"],
            "Recording 20250115090000_1.mp3": ["source-model"],
        }

    def test_merge_moves_additional_files_and_renames_collisions(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """User-added top-level files survive merge without overwriting target files."""
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115080000.mp3",
            bundle_date=datetime(2025, 1, 15, 8, tzinfo=fake_config.general.timezone),
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_current",
            audio_filename="Recording 20250115090000.mp3",
            bundle_date=datetime(2025, 1, 15, 9, tzinfo=fake_config.general.timezone),
            commands=["merge"],
        )
        assert current_bundle.commands is not None

        target_dir = previous_bundle.get_bundle_dir()
        source_dir = current_bundle.get_bundle_dir()
        fake_fs.write_file(target_dir / "notes.txt", "target notes")
        fake_fs.write_file(target_dir / "notes_1.txt", "earlier collision")
        fake_fs.write_file(source_dir / "notes.txt", "source notes")
        fake_fs.write_file(source_dir / "attachment.pdf", "source attachment")

        _execute_previous_merge(
            current_bundle,
            fake_config,
            _bundle_cache(previous_bundle, current_bundle),
            current_bundle.commands.commands[0],
        )

        assert fake_fs.read_file(target_dir / "notes.txt") == "target notes"
        assert fake_fs.read_file(target_dir / "notes_1.txt") == "earlier collision"
        assert fake_fs.read_file(target_dir / "notes_2.txt") == "source notes"
        assert fake_fs.read_file(target_dir / "attachment.pdf") == "source attachment"

    def test_merge_moves_directory_trees_and_renames_collisions(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """A top-level directory moves intact under one collision-safe name."""
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115080000.mp3",
            bundle_date=datetime(2025, 1, 15, 8, tzinfo=fake_config.general.timezone),
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_current",
            audio_filename="Recording 20250115090000.mp3",
            bundle_date=datetime(2025, 1, 15, 9, tzinfo=fake_config.general.timezone),
            commands=["merge"],
        )
        assert current_bundle.commands is not None

        target_dir = previous_bundle.get_bundle_dir()
        source_dir = current_bundle.get_bundle_dir()
        fake_fs.write_file(target_dir / "attachments" / "target.txt", "target attachment")
        fake_fs.write_file(target_dir / "attachments_1" / "earlier.txt", "earlier collision")
        fake_fs.write_file(source_dir / "attachments" / "nested" / "source.txt", "source attachment")
        fake_fs.write_file(source_dir / "photos" / "image.jpg", "source photo")

        _execute_previous_merge(
            current_bundle,
            fake_config,
            _bundle_cache(previous_bundle, current_bundle),
            current_bundle.commands.commands[0],
        )

        assert fake_fs.read_file(target_dir / "attachments" / "target.txt") == "target attachment"
        assert fake_fs.read_file(target_dir / "attachments_1" / "earlier.txt") == "earlier collision"
        assert fake_fs.read_file(target_dir / "attachments_2" / "nested" / "source.txt") == "source attachment"
        assert fake_fs.read_file(target_dir / "photos" / "image.jpg") == "source photo"

    def test_merge_does_not_move_managed_files_as_additional_files(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Application-owned files keep their semantic merge behavior."""
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115080000.mp3",
            bundle_date=datetime(2025, 1, 15, 8, tzinfo=fake_config.general.timezone),
            transcript_text="target transcript",
            summary_text="target summary",
            commands=["target command"],
            custom_context="target context",
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_current",
            audio_filename="Recording 20250115090000.mp3",
            bundle_date=datetime(2025, 1, 15, 9, tzinfo=fake_config.general.timezone),
            transcript_text="source transcript",
            summary_text="source summary",
            commands=["merge"],
            custom_context="source context",
        )
        assert current_bundle.commands is not None

        _execute_previous_merge(
            current_bundle,
            fake_config,
            _bundle_cache(previous_bundle, current_bundle),
            current_bundle.commands.commands[0],
        )

        target_dir = previous_bundle.get_bundle_dir()
        collision_copies = {
            "_metadata_1.md",
            "transcript_1.md",
            "summary_1.md",
            "_commands_1.md",
            "custom_context_1.md",
            "_merge_failed_1.md",
        }
        assert collision_copies.isdisjoint(path.name for path in fake_fs.list_directory(target_dir))

    def test_merge_merges_transcript_model_and_keep_forever_edge_cases(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify transcript_model_used dedup/None handling and keep_forever promotion."""
        # Previous has no model, current has one -> model carried over from current
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
            transcript_text="previous transcript",
            transcript_model_used=None,
            keep_forever=False,
        )
        # Current reuses the same model as previous would have, plus keep_forever True
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            transcript_text="current transcript",
            transcript_model_used="model-a",
            keep_forever=True,
            commands=["merge"],
        )
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)

        _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])

        # Model carried over from current since previous had none
        # (previous bundle's file has no model, current bundle's file has ['model-a'])
        assert previous_bundle.metadata.audio_files[0].transcript_model_used == []
        assert previous_bundle.metadata.audio_files[1].transcript_model_used == ["model-a"]
        # keep_forever promoted to True because current bundle set it
        assert previous_bundle.metadata.keep_forever is True

        # Now merge again with a bundle that reuses the same model and no keep_forever.
        # Its timestamp (12:00) must be strictly later than the merged previous
        # bundle's first audio (01:00) and within the 12h merge window.
        third_model = "model-a"
        third_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_later",
            audio_filename="Recording 20250115120000.mp3",
            transcript_text="third transcript",
            transcript_model_used=third_model,
            keep_forever=False,
            commands=["merge"],
        )
        assert third_bundle.commands
        third_bundle_dir = third_bundle.get_bundle_dir()
        bundle_cache[third_bundle.bundle_id] = third_bundle

        _execute_previous_merge(third_bundle, fake_config, bundle_cache, third_bundle.commands.commands[0])

        # Duplicate model is not appended again (per-file models travel with their files)
        # After two merges we have 3 files: first has [], second has ['model-a'], third has ['model-a']
        assert previous_bundle.metadata.audio_files[0].transcript_model_used == []
        assert previous_bundle.metadata.audio_files[1].transcript_model_used == ["model-a"]
        assert previous_bundle.metadata.audio_files[2].transcript_model_used == ["model-a"]
        # keep_forever stays True from the earlier merge
        assert previous_bundle.metadata.keep_forever is True
        assert not fake_fs.directory_exists(third_bundle_dir)

    def test_merge_concatenates_custom_context(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Merge concatenates the source's custom_context into the target."""
        previous_context = "Context about the previous recording.\n"
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
            transcript_text="previous transcript",
            custom_context=previous_context,
        )
        current_context = "Context about the current recording.\n"
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            transcript_text="current transcript",
            commands=["merge"],
            custom_context=current_context,
        )
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)
        previous_bundle_dir = previous_bundle.get_bundle_dir()
        current_bundle_dir = current_bundle.get_bundle_dir()

        merge_cmd = current_bundle.commands.commands[0]
        _execute_previous_merge(current_bundle, fake_config, bundle_cache, merge_cmd)

        assert not fake_fs.directory_exists(current_bundle_dir)

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        # Both contexts are present in the merged bundle (concatenated, target first).
        assert merged_bundle.get_effective_context() == (f"{previous_context.strip()}\n\n{current_context.strip()}")

    def test_merge_concatenates_different_transcript_models(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify different transcript models from both bundles are both present after merge."""
        previous_model = "whisper"
        current_model = "gpt-4o-transcribe"
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
            transcript_text="previous transcript",
            transcript_model_used=previous_model,
            keep_forever=False,
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            transcript_text="current transcript",
            transcript_model_used=current_model,
            keep_forever=False,
            commands=["merge"],
        )
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)

        previous_bundle_dir = previous_bundle.get_bundle_dir()

        _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Both models should be present in the merged metadata, regardless of exact syntax.
        merged_models = [model for audio_meta in merged_bundle.metadata.audio_files for model in audio_meta.transcript_model_used]
        assert previous_model in merged_models
        assert current_model in merged_models

    def test_merge_keeps_previous_transcript_when_current_has_none(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify previous transcript is preserved when current bundle has no transcript."""
        previous_transcript_text = "previous transcript"
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
            transcript_text=previous_transcript_text,
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            commands=["merge"],
        )
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)

        previous_bundle_dir = previous_bundle.get_bundle_dir()
        current_bundle_dir = current_bundle.get_bundle_dir()

        _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])

        assert not fake_fs.directory_exists(current_bundle_dir)

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Previous transcript is kept as-is, no separator injected, no data loss.
        assert merged_bundle.transcript is not None
        assert merged_bundle.transcript.text == previous_transcript_text
        assert MULTIPLE_TRANSCRIPTS_SEPARATOR not in merged_bundle.transcript.text

    def test_merge_promotes_current_transcript_when_previous_has_none(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify current transcript is promoted when previous bundle has no transcript."""
        current_transcript_text = "current transcript"
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            transcript_text=current_transcript_text,
            commands=["merge"],
        )
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)

        previous_bundle_dir = previous_bundle.get_bundle_dir()
        current_bundle_dir = current_bundle.get_bundle_dir()

        _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])

        assert not fake_fs.directory_exists(current_bundle_dir)

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Current transcript is carried over (previously this would have been lost).
        assert merged_bundle.transcript is not None
        assert merged_bundle.transcript.text == current_transcript_text
        assert MULTIPLE_TRANSCRIPTS_SEPARATOR not in merged_bundle.transcript.text

    def test_merge_has_no_transcript_when_neither_bundle_has_one(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify merged bundle has no transcript when neither source has one."""
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            commands=["merge"],
        )
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)

        previous_bundle_dir = previous_bundle.get_bundle_dir()
        current_bundle_dir = current_bundle.get_bundle_dir()

        _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])

        assert not fake_fs.directory_exists(current_bundle_dir)

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        assert merged_bundle.transcript is None

    def test_merge_copies_source_commands_when_target_has_none(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify source commands are copied when the target bundle has no commands."""
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            commands=["merge", "do something"],
        )
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)

        previous_bundle_dir = previous_bundle.get_bundle_dir()
        current_bundle_dir = current_bundle.get_bundle_dir()

        merge_cmd = current_bundle.commands.commands[0]
        other_cmd = current_bundle.commands.commands[1]
        _execute_previous_merge(current_bundle, fake_config, bundle_cache, merge_cmd)

        assert not fake_fs.directory_exists(current_bundle_dir)

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Target had no commands; source commands are copied over and preserved.
        assert merged_bundle.commands is not None
        commands = merged_bundle.commands.commands
        assert [cmd.text for cmd in commands] == [merge_cmd.text, other_cmd.text]
        # Mutation preserves unresolved commands. The request lifecycle writes
        # the terminal receipt after the executor returns.
        assert commands[0].executed is False
        assert commands[1].executed is False

    def test_merge_removes_failure_marker_on_success(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """On a successful merge, the _merge_failed.md cue is cleared."""
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            commands=["merge"],
        )
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)
        previous_bundle_dir = previous_bundle.get_bundle_dir()

        _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])

        assert not fake_fs.file_exists(previous_bundle_dir / MERGE_FAILED_FILENAME)

    def test_merge_leaves_marker_and_source_on_failure(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """A failed merge keeps the source bundle (and its audio) and writes a failure cue.

        The merge command must NOT be marked executed, so the merge stays retryable.
        """
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            commands=["merge"],
        )
        assert current_bundle.commands
        bundle_cache = _bundle_cache(current_bundle, previous_bundle)

        previous_bundle_dir = previous_bundle.get_bundle_dir()
        current_bundle_dir = current_bundle.get_bundle_dir()

        # Make the target metadata write fail to simulate a mid-merge crash.
        original_write = fake_fs.write_file

        def _failing_write(path: Path, content: str) -> None:
            if path.name == "_metadata.md":
                msg = "simulated metadata write failure"
                raise OSError(msg)
            original_write(path, content)

        fake_fs.write_file = _failing_write  # type: ignore[method-assign]

        # The merge aborts; the original exception propagates.
        with pytest.raises(OSError, match="simulated metadata write failure"):
            _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])

        # Source bundle (and its audio) is preserved and retryable.
        assert fake_fs.directory_exists(current_bundle_dir)
        assert fake_fs.file_exists(current_bundle_dir / "Recording 20250115090000.mp3")
        # Failure cue is present in BOTH the target and the source bundle.
        assert fake_fs.file_exists(previous_bundle_dir / MERGE_FAILED_FILENAME)
        assert fake_fs.file_exists(current_bundle_dir / MERGE_FAILED_FILENAME)
        # Merge command is NOT marked executed, so the merge can be retried.
        assert current_bundle.commands is not None
        assert current_bundle.commands.commands[0].executed is False

        # A subsequent merge attempt into the same (marked) target is blocked, so the
        # system does not auto-retry against a potentially inconsistent target.
        with pytest.raises(MergeBlockedException):
            _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])
        # The markers are still present after the blocked attempt.
        assert fake_fs.file_exists(previous_bundle_dir / MERGE_FAILED_FILENAME)
        assert fake_fs.file_exists(current_bundle_dir / MERGE_FAILED_FILENAME)

        # A subsequent merge attempt into the same (marked) target is blocked, so the
        # system does not auto-retry against a potentially inconsistent target.
        with pytest.raises(MergeBlockedException):
            _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])
        # The marker is still present after the blocked attempt.
        assert fake_fs.file_exists(previous_bundle_dir / MERGE_FAILED_FILENAME)

    def test_merge_blocks_further_bundle_merging_into_failed_target(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Both source and target markers block retries, independent of bundle order.

        A failed merge writes a marker into BOTH bundles. The source marker protects
        the source even when it later becomes the target of a newer bundle (i.e. its
        resolved predecessor shifts), which the target-only marker would miss.
        """
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            commands=["merge"],
        )
        assert current_bundle.commands
        previous_bundle_dir = previous_bundle.get_bundle_dir()
        current_bundle_dir = current_bundle.get_bundle_dir()

        # Simulate a failed merge: markers already present in BOTH bundles.
        fake_fs.write_file(
            previous_bundle_dir / MERGE_FAILED_FILENAME,
            "Merge of 2025-01-15_meeting into 2025-01-15_previous did not complete.\n",
        )
        fake_fs.write_file(
            current_bundle_dir / MERGE_FAILED_FILENAME,
            "Merge of 2025-01-15_meeting into 2025-01-15_previous did not complete.\n",
        )

        bundle_cache = _bundle_cache(current_bundle, previous_bundle)

        # The original source retrying into the failed target is blocked.
        with pytest.raises(MergeBlockedException):
            _execute_previous_merge(current_bundle, fake_config, bundle_cache, current_bundle.commands.commands[0])

        # A newer bundle merges into its immediate predecessor, which is the failed
        # source (meeting, 09:00). Because the source carries its own marker, this
        # merge is blocked too -- the target-only marker would have missed this.
        newer_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_later",
            audio_filename="Recording 20250115120000.mp3",
            commands=["merge"],
        )
        assert newer_bundle.commands
        bundle_cache[newer_bundle.bundle_id] = newer_bundle
        with pytest.raises(MergeBlockedException):
            _execute_previous_merge(newer_bundle, fake_config, bundle_cache, newer_bundle.commands.commands[0])

        # Neither source bundle was deleted; both markers remain.
        assert fake_fs.directory_exists(current_bundle_dir)
        assert fake_fs.file_exists(previous_bundle_dir / MERGE_FAILED_FILENAME)
        assert fake_fs.file_exists(current_bundle_dir / MERGE_FAILED_FILENAME)

    def test_merge_merges_transcript_models_as_ordered_set(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify models merge as an exact-match ordered set, not a substring join."""
        # These names are intentionally chosen so a substring check would wrongly dedup.
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
            transcript_text="previous transcript",
            transcript_model_used=["whisper", "whisper-large"],
            keep_forever=False,
            commands=["merge"],
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            transcript_text="current transcript",
            transcript_model_used=["whisper-large", "gpt-4o-transcribe"],
            keep_forever=False,
            commands=["merge"],
        )
        assert current_bundle.commands

        previous_bundle_dir = previous_bundle.get_bundle_dir()

        _execute_previous_merge(
            current_bundle,
            fake_config,
            _bundle_cache(current_bundle, previous_bundle),
            current_bundle.commands.commands[0],
        )

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Per-file models: each file keeps its own model list (no cross-file dedup).
        # Previous file: ['whisper', 'whisper-large'], Current file: ['whisper-large', 'gpt-4o-transcribe']
        assert merged_bundle.metadata.audio_files[0].transcript_model_used == [
            "whisper",
            "whisper-large",
        ]
        assert merged_bundle.metadata.audio_files[1].transcript_model_used == [
            "whisper-large",
            "gpt-4o-transcribe",
        ]

    def test_merge_fails_when_previous_bundle_is_too_old(
        self,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify merge rejects a previous bundle that is older than the configured merge window."""
        transcribe_bundle_factory(
            bundle_name="2025-01-14_previous",
            audio_filename="Recording 20250114090000.mp3",
            bundle_date=datetime(year=2025, month=1, day=14, tzinfo=fake_config.general.timezone),
        )

        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            commands=["merge"],
        )
        assert current_bundle.commands

        merge_cmd = current_bundle.commands.commands[0]
        result = _execute_previous_merge(current_bundle, fake_config, _bundle_cache(current_bundle), merge_cmd)

        assert isinstance(result, ActionFailed)
        assert result.error.code == "target_not_found"

    def test_merge_fails_without_previous_bundle(
        self,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify merge raises a clear error when there is no prior bundle to merge into."""
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="curr.mp3",
            commands=["merge"],
        )

        assert current_bundle.commands
        merge_cmd = current_bundle.commands.commands[0]
        result = _execute_previous_merge(current_bundle, fake_config, _bundle_cache(current_bundle), merge_cmd)

        assert isinstance(result, ActionFailed)
        assert result.error.code == "target_not_found"

    def test_merge_preserves_manual_target_title_state(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Merging new content does not unlock the target's explicit title."""
        target = transcribe_bundle_factory(
            bundle_name="2025-01-15_manual-target",
            audio_filename="Recording 20250115010000.mp3",
        )
        target.metadata.bundle_title_state = BundleTitleState.MANUAL
        source = transcribe_bundle_factory(
            bundle_name="2025-01-15_source",
            audio_filename="Recording 20250115090000.mp3",
            commands=["merge"],
        )
        assert source.commands is not None

        _execute_previous_merge(source, fake_config, _bundle_cache(target, source), source.commands.commands[0])

        reloaded_target = TranscribeBundle.from_existing_directory(
            target.get_bundle_dir(),
            fake_config,
            fake_fs,
            fake_audio_service,
        )
        assert reloaded_target.metadata.bundle_title_state is BundleTitleState.MANUAL


class TestSetTitleActionExecutor:
    """Tests for applying a title action to a bundle."""

    def test_executor_applies_manual_normalized_title(
        self,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """The executor delegates normalization and persists manual provenance."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_recording",
            audio_filename="recording.mp3",
        )
        bundle_cache = _bundle_cache(bundle)

        BundleActionExecutor(bundle_cache).execute(
            SetTitleAction(
                bundle_id=bundle.bundle_id,
                title="  Quarterly / Planning  ",
            ),
        )

        assert bundle.bundle_name.endswith("Quarterly Planning")
        assert bundle.metadata.bundle_title_state is BundleTitleState.MANUAL
