"""Interactively review and merge recording bundles that may belong together.

Candidate groups are transitive. Bundles are connected when their dates are less
than the configured gap apart, or when they share a calendar day and either date
uses midnight to represent an unknown time. Nothing is changed until a reviewed
ordering reaches its explicit confirmation step.

Usage:
    uv run python scripts/review_merge_candidates.py
    uv run python scripts/review_merge_candidates.py --store-dir D:/recordings/store
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Literal

from transcriber.audio_service import RealAudioService
from transcriber.commands.command_handlers import merge_bundles
from transcriber.config import TranscribeConfig
from transcriber.constants import MERGE_FAILED_FILENAME
from transcriber.files.file_system import RealFileSystemService
from transcriber.files.text_file import TranscriptFile
from transcriber.transcribe_bundle import BundleCache, TranscribeBundle

DEFAULT_MAX_GAP_HOURS = 2.0
DEFAULT_SNAPSHOT_PATH = Path(".current_merge_candidates.txt")
SECTION_RULE = "=" * 88
NO_TRANSCRIPT_TEXT = "[No transcript]"

Output = Callable[[str], None]
Prompt = Callable[[str], str]
ReviewAction = Literal["commit", "skip", "quit"]
EditAction = Literal["finish", "skip", "quit"]


@dataclass(frozen=True)
class ReviewDecision:
    """Result of reviewing one candidate group."""

    action: ReviewAction
    ordered_bundles: tuple[TranscribeBundle, ...] = ()


@dataclass
class _DisjointSet:
    """Track transitive candidate connectivity by list index."""

    parents: list[int]

    @classmethod
    def with_size(cls, size: int) -> _DisjointSet:
        return cls(parents=list(range(size)))

    def find(self, index: int) -> int:
        while self.parents[index] != index:
            self.parents[index] = self.parents[self.parents[index]]
            index = self.parents[index]
        return index

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parents[right_root] = left_root


@dataclass
class _ReviewState:
    """Mutable ordering state for one otherwise read-only review."""

    candidate_count: int
    ordered_indices: list[int] = field(default_factory=list)
    excluded_indices: set[int] = field(default_factory=set)

    def add(self, candidate_index: int) -> None:
        if candidate_index in self.excluded_indices:
            msg = "That candidate is excluded; include it before adding it."
            raise ValueError(msg)
        if candidate_index in self.ordered_indices:
            msg = "That candidate is already in the ordering."
            raise ValueError(msg)
        self.ordered_indices.append(candidate_index)

    def add_all(self) -> None:
        self.ordered_indices.extend(
            index for index in range(self.candidate_count) if index not in self.ordered_indices and index not in self.excluded_indices
        )

    def exclude(self, candidate_index: int) -> None:
        if candidate_index in self.ordered_indices:
            self.ordered_indices.remove(candidate_index)
        self.excluded_indices.add(candidate_index)

    def include(self, candidate_index: int) -> None:
        self.excluded_indices.discard(candidate_index)

    def undo(self) -> None:
        if not self.ordered_indices:
            msg = "The ordering is already empty."
            raise ValueError(msg)
        self.ordered_indices.pop()

    def reset(self) -> None:
        self.ordered_indices.clear()
        self.excluded_indices.clear()


def _has_unknown_time(bundle: TranscribeBundle) -> bool:
    bundle_time = bundle.get_bundle_date().timetz()
    return bundle_time.hour == 0 and bundle_time.minute == 0 and bundle_time.second == 0 and bundle_time.microsecond == 0


def find_candidate_groups(
    bundles: Iterable[TranscribeBundle],
    *,
    max_gap: timedelta,
) -> list[list[TranscribeBundle]]:
    """Return transitive candidate groups in chronological order.

    Consecutive dates closer than ``max_gap`` connect into the same component.
    In addition, a midnight bundle connects every bundle from its calendar day,
    because midnight represents a recording whose exact time is unknown.
    """
    if max_gap <= timedelta(0):
        msg = "Candidate gap must be greater than zero"
        raise ValueError(msg)

    ordered = sorted(
        bundles,
        key=lambda bundle: (bundle.get_bundle_date(), bundle.bundle_id),
    )
    connectivity = _DisjointSet.with_size(len(ordered))

    # For a one-dimensional time-gap graph, joining eligible neighbors produces
    # the same transitive components as comparing every possible pair.
    for index in range(1, len(ordered)):
        previous_date = ordered[index - 1].get_bundle_date()
        current_date = ordered[index].get_bundle_date()
        if current_date - previous_date < max_gap:
            connectivity.union(index - 1, index)

    indices_by_day: dict[object, list[int]] = defaultdict(list)
    for index, bundle in enumerate(ordered):
        indices_by_day[bundle.get_bundle_date().date()].append(index)

    for day_indices in indices_by_day.values():
        unknown_time_index = next(
            (index for index in day_indices if _has_unknown_time(ordered[index])),
            None,
        )
        if unknown_time_index is not None:
            for index in day_indices:
                connectivity.union(unknown_time_index, index)

    grouped_indices: dict[int, list[int]] = defaultdict(list)
    for index in range(len(ordered)):
        grouped_indices[connectivity.find(index)].append(index)

    candidate_groups = [[ordered[index] for index in indices] for indices in grouped_indices.values() if len(indices) >= 2]
    candidate_groups.sort(key=lambda group: (group[0].get_bundle_date(), group[0].bundle_id))
    return candidate_groups


def format_transcript_preview(bundles: Sequence[TranscribeBundle]) -> str:
    """Combine transcripts in order with stable identity and title separators."""
    sections: list[str] = []
    for position, bundle in enumerate(bundles, start=1):
        role = "SURVIVES" if position == 1 else f"MERGE #{position - 1}"
        transcript = bundle.transcript.text if bundle.transcript else NO_TRANSCRIPT_TEXT
        sections.append(
            "\n".join(
                (
                    SECTION_RULE,
                    f"{position}. {role}",
                    f"ID:    {bundle.bundle_id}",
                    f"Title: {bundle.bundle_name}",
                    f"Date:  {bundle.get_bundle_date().isoformat()}",
                    SECTION_RULE,
                    transcript,
                ),
            ),
        )
    return "\n\n".join(sections)


def _format_candidate_list(
    candidates: Sequence[TranscribeBundle],
    ordered_indices: Sequence[int],
    excluded_indices: set[int],
) -> str:
    order_by_index = {candidate_index: order for order, candidate_index in enumerate(ordered_indices, start=1)}
    lines = ["Candidates:"]
    for index, bundle in enumerate(candidates, start=1):
        zero_based_index = index - 1
        if zero_based_index in order_by_index:
            status = f"merge position {order_by_index[zero_based_index]}"
        elif zero_based_index in excluded_indices:
            status = "not merged"
        else:
            status = "not ordered"
        lines.append(
            f"  {index:>2}. [{status}] {bundle.get_bundle_date().isoformat()} | {bundle.bundle_id} | {bundle.bundle_name}",
        )
    return "\n".join(lines)


def _format_review_state(
    candidates: Sequence[TranscribeBundle],
    ordered_indices: Sequence[int],
    excluded_indices: set[int],
) -> str:
    lines = [SECTION_RULE, "CURRENT WOULD-BE RESULT", SECTION_RULE]
    lines.append(_format_candidate_list(candidates, ordered_indices, excluded_indices))
    if ordered_indices:
        ordered_bundles = [candidates[index] for index in ordered_indices]
        lines.extend(("", format_transcript_preview(ordered_bundles)))
    else:
        lines.extend(("", "No bundles selected yet."))
    return "\n".join(lines)


def _format_finish_summary(
    candidates: Sequence[TranscribeBundle],
    ordered_indices: Sequence[int],
) -> str:
    """Show the confirmed merge order without repeating transcript text."""
    lines = [SECTION_RULE, "FINAL MERGE ORDER", SECTION_RULE]
    for position, candidate_index in enumerate(ordered_indices, start=1):
        bundle = candidates[candidate_index]
        role = "SURVIVES" if position == 1 else f"MERGE #{position - 1}"
        lines.append(
            f"  {position:>2}. {role} | {bundle.get_bundle_date().isoformat()} | "
            f"{bundle.bundle_id} | {bundle.bundle_name}",
        )

    untouched_indices = [index for index in range(len(candidates)) if index not in ordered_indices]
    if untouched_indices:
        lines.append("NOT MERGED:")
        for candidate_index in untouched_indices:
            bundle = candidates[candidate_index]
            lines.append(
                f"      {bundle.get_bundle_date().isoformat()} | {bundle.bundle_id} | {bundle.bundle_name}",
            )
    return "\n".join(lines)


def _parse_candidate_number(raw_value: str, candidate_count: int) -> int:
    try:
        one_based_index = int(raw_value)
    except ValueError as error:
        msg = f"Expected a candidate number, got {raw_value!r}"
        raise ValueError(msg) from error
    if not 1 <= one_based_index <= candidate_count:
        msg = f"Candidate number must be between 1 and {candidate_count}"
        raise ValueError(msg)
    return one_based_index - 1


def _apply_review_command(response: str, state: _ReviewState) -> EditAction | None:
    """Apply one editing command, returning only workflow-level actions."""
    parts = response.split()
    if not parts:
        msg = "Please enter a command."
        raise ValueError(msg)

    if len(parts) == 1 and parts[0].isdecimal():
        state.add(_parse_candidate_number(parts[0], state.candidate_count))
        return None

    if len(parts) == 1:
        workflow_actions: dict[str, EditAction] = {
            "finish": "finish",
            "skip-group": "skip",
            "quit": "quit",
        }
        workflow_action = workflow_actions.get(parts[0])
        if workflow_action is not None:
            return workflow_action

        state_operation = {
            "all": state.add_all,
            "undo": state.undo,
            "reset": state.reset,
        }.get(parts[0])
        if state_operation is not None:
            state_operation()
            return None

    if len(parts) == 2:
        candidate_operation = {
            "add": state.add,
            "exclude": state.exclude,
            "include": state.include,
        }.get(parts[0])
        if candidate_operation is not None:
            candidate_operation(_parse_candidate_number(parts[1], state.candidate_count))
            return None

    msg = f"Unknown command: {response!r}"
    raise ValueError(msg)


def _confirm_review(prompt: Prompt) -> ReviewAction | Literal["edit"]:
    confirmation = prompt("Commit this reviewed merge? [yes/edit/skip/quit] ").strip().casefold()
    confirmed_actions: dict[str, ReviewAction] = {
        "yes": "commit",
        "y": "commit",
        "skip": "skip",
        "s": "skip",
        "quit": "quit",
        "q": "quit",
    }
    return confirmed_actions.get(confirmation, "edit")


def _decision_for_action(
    action: EditAction | ReviewAction | Literal["edit"] | None,
    selected: tuple[TranscribeBundle, ...] = (),
) -> ReviewDecision | None:
    """Translate a completed workflow action into a review decision."""
    if action == "commit":
        return ReviewDecision(action="commit", ordered_bundles=selected)
    if action == "skip":
        return ReviewDecision(action="skip")
    if action == "quit":
        return ReviewDecision(action="quit")
    return None


def review_candidate_group(
    candidates: Sequence[TranscribeBundle],
    *,
    prompt: Prompt = input,
    output: Output = print,
    snapshot_path: Path | None = None,
) -> ReviewDecision:
    """Build and confirm one merge order without mutating any bundle."""
    if len(candidates) < 2:
        msg = "A review group must contain at least two bundles"
        raise ValueError(msg)

    output("Candidate transcripts in chronological order:")
    output(format_transcript_preview(candidates))
    state = _ReviewState(candidate_count=len(candidates))

    help_text = (
        "Commands: NUMBER or 'add NUMBER', 'all', 'exclude NUMBER', 'include NUMBER', 'undo', 'reset', 'finish', 'skip-group', 'quit'."
    )

    while True:
        review_state = _format_review_state(candidates, state.ordered_indices, state.excluded_indices)
        output(review_state)
        if snapshot_path is not None:
            snapshot_path.write_text(
                f"Candidate transcripts in chronological order:\n{format_transcript_preview(candidates)}\n\n{review_state}\n",
                encoding="utf-8",
            )
        output(help_text)
        response = prompt("review> ").strip().casefold()
        try:
            action = _apply_review_command(response, state)
        except ValueError as error:
            output(str(error))
            continue

        if decision := _decision_for_action(action):
            return decision
        if action != "finish":
            continue
        if len(state.ordered_indices) < 2:
            output("Select at least two bundles before finishing.")
            continue

        selected = tuple(candidates[index] for index in state.ordered_indices)
        output(_format_finish_summary(candidates, state.ordered_indices))
        output(
            "This commit performs sequential, irreversible merges and stops at the first failure. "
            "The first selected bundle survives; every candidate not in the ordering remains untouched.",
        )
        confirmation = _confirm_review(prompt)
        if decision := _decision_for_action(confirmation, selected):
            return decision
        output("Returning to editing without changing anything.")


def _preflight_merge_plan(ordered_bundles: Sequence[TranscribeBundle], bundles_cache: BundleCache) -> None:
    """Reject detectable plan problems before the first irreversible merge."""
    if len(ordered_bundles) < 2:
        msg = "A merge plan must contain at least two bundles"
        raise ValueError(msg)

    bundle_ids = [bundle.bundle_id for bundle in ordered_bundles]
    if len(set(bundle_ids)) != len(bundle_ids):
        msg = "A merge plan cannot contain the same bundle more than once"
        raise ValueError(msg)

    for bundle in ordered_bundles:
        if bundle.bundle_id not in bundles_cache:
            msg = f"Bundle is no longer loaded: {bundle.bundle_id} ({bundle.bundle_name})"
            raise ValueError(msg)
        bundle_dir = bundle.get_bundle_dir()
        if not bundle.fs_service.directory_exists(bundle_dir):
            msg = f"Bundle directory no longer exists: {bundle_dir}"
            raise FileNotFoundError(msg)
        marker_path = bundle_dir / MERGE_FAILED_FILENAME
        if bundle.fs_service.file_exists(marker_path):
            msg = f"Merge is blocked by an existing failure marker: {marker_path}"
            raise RuntimeError(msg)
        for audio_path in bundle.source_audios:
            if not bundle.fs_service.file_exists(audio_path):
                msg = f"Audio file no longer exists: {audio_path}"
                raise FileNotFoundError(msg)


def commit_merge_plan(
    ordered_bundles: Sequence[TranscribeBundle],
    bundles_cache: BundleCache,
    *,
    output: Output = print,
) -> TranscribeBundle:
    """Apply a reviewed order, retaining the first bundle as the target."""
    _preflight_merge_plan(ordered_bundles, bundles_cache)
    target = ordered_bundles[0]
    for source in ordered_bundles[1:]:
        output(
            f"Merging {source.bundle_id} ({source.bundle_name}) into {target.bundle_id} ({target.bundle_name})...",
        )
        target = merge_bundles(
            source=source,
            target=target,
            bundles_cache=bundles_cache,
        )
    output(f"Committed merge. Surviving bundle: {target.bundle_id} ({target.bundle_name})")
    return target


def review_candidate_merges(
    candidate_groups: Sequence[Sequence[TranscribeBundle]],
    bundles_cache: BundleCache,
    *,
    prompt: Prompt = input,
    output: Output = print,
    snapshot_path: Path | None = None,
) -> int:
    """Review groups in order and return the number of committed groups."""
    committed_groups = 0
    for group_number, candidates in enumerate(candidate_groups, start=1):
        output(f"\n{SECTION_RULE}\nGROUP {group_number} OF {len(candidate_groups)}\n{SECTION_RULE}")
        decision = review_candidate_group(
            candidates,
            prompt=prompt,
            output=output,
            snapshot_path=snapshot_path,
        )
        if decision.action == "quit":
            output("Stopped by user. No unconfirmed group was changed.")
            break
        if decision.action == "skip":
            output("Skipped group without changes.")
            continue
        commit_merge_plan(decision.ordered_bundles, bundles_cache, output=output)
        committed_groups += 1
    return committed_groups


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Bundle store directory. Defaults to general.store_dir.",
    )
    parser.add_argument(
        "--max-hours",
        type=float,
        default=DEFAULT_MAX_GAP_HOURS,
        help=f"Strict maximum gap between known bundle dates (default: {DEFAULT_MAX_GAP_HOURS:g}).",
    )
    parser.add_argument(
        "--snapshot-file",
        type=Path,
        default=DEFAULT_SNAPSHOT_PATH,
        help=f"File overwritten with the current review state (default: {DEFAULT_SNAPSHOT_PATH}).",
    )
    args = parser.parse_args()
    if args.max_hours <= 0:
        parser.error("--max-hours must be greater than zero")
    return args


def main() -> None:
    """Load existing bundles and run the interactive review."""
    args = _parse_args()
    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]
    if args.store_dir is not None:
        config.general.store_dir = args.store_dir

    fs_service = RealFileSystemService(config)
    bundles_cache = TranscribeBundle.gather_existing_bundles(
        store_dir=config.general.store_dir,
        dry_run=True,
        cleanup_bundle=False,
        config=config,
        fs_service=fs_service,
        audio_service=RealAudioService(),
    )

    candidate_groups = find_candidate_groups(
        bundles_cache.values(),
        max_gap=timedelta(hours=args.max_hours),
    )
    print(
        f"Loaded {len(bundles_cache)} bundle(s) from {config.general.store_dir}. Found {len(candidate_groups)} candidate group(s).",
    )
    if not candidate_groups:
        return

    try:
        committed_groups = review_candidate_merges(
            candidate_groups,
            bundles_cache,
            snapshot_path=args.snapshot_file,
        )
    except (EOFError, KeyboardInterrupt):
        print("\nStopped by user. No unconfirmed group was changed.")
        return
    except Exception:
        print(
            "\nA committed merge failed. Review the affected bundle directories and "
            f"any {MERGE_FAILED_FILENAME} markers. No later group was attempted.",
        )
        raise

    print(f"Review complete. Committed {committed_groups} group(s).")


if __name__ == "__main__":
    main()
