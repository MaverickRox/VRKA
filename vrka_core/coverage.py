"""Pure VOD coverage model for browser-context HLS transfer.

Tracks expected versus captured segment indices for one media lineage and
answers the questions the coverage controller asks every cycle:

- which ranges are missing (as inclusive index ranges)?
- where should the player seek next (first missing segment's start time)?
- is coverage complete?

Deterministic, offline, no browser/network/GUI dependencies.  Per-lineage
coverage is a dict of these models keyed by lineage, owned by the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_EXTINF_RE = re.compile(r"#EXTINF:([\d.]+)")


@dataclass
class MissingRange:
    first: int  # inclusive
    last: int   # inclusive
    start_time: float

    @property
    def length(self) -> int:
        return self.last - self.first + 1


@dataclass
class CoverageModel:
    """Expected segment start times (index-ordered) + captured index set."""

    segment_times: list[float]
    captured: set[int] = field(default_factory=set)

    def mark_captured(self, index: int) -> None:
        if 0 <= index < len(self.segment_times):
            self.captured.add(index)

    def mark_range_captured(self, first: int, last: int) -> None:
        for index in range(first, last + 1):
            self.mark_captured(index)

    def is_captured(self, index: int) -> bool:
        return index in self.captured

    def missing_ranges(self) -> list[MissingRange]:
        ranges: list[MissingRange] = []
        run_start = None
        for index in range(len(self.segment_times)):
            if index not in self.captured:
                if run_start is None:
                    run_start = index
            elif run_start is not None:
                ranges.append(MissingRange(
                    run_start, index - 1, self.segment_times[run_start]))
                run_start = None
        if run_start is not None:
            ranges.append(MissingRange(
                run_start, len(self.segment_times) - 1,
                self.segment_times[run_start]))
        return ranges

    def first_missing(self) -> MissingRange | None:
        ranges = self.missing_ranges()
        return ranges[0] if ranges else None

    def largest_missing(self) -> MissingRange | None:
        ranges = self.missing_ranges()
        return max(ranges, key=lambda r: r.length) if ranges else None

    def coverage_fraction(self) -> float:
        if not self.segment_times:
            return 0.0
        return len(self.captured) / len(self.segment_times)

    def is_complete(self) -> bool:
        return bool(self.segment_times) and len(self.captured) == len(self.segment_times)

    def next_target_time(self) -> float | None:
        """Seek target for the next gap fill: the first missing segment's
        start time (the player fetches forward from the seek position, so
        aiming at the gap start covers the gap in playback order)."""
        missing = self.first_missing()
        return missing.start_time if missing else None


def parse_playlist(text: str, playlist_url: str) -> tuple[list[float], list[str]]:
    """Parse a media playlist into (segment start times, segment URLs).

    URLs are resolved against the playlist URL; query strings are dropped
    (signed per-request queries must not break index matching).
    """
    origin = "/".join(playlist_url.split("/")[:3])
    base = playlist_url.split("?")[0].rsplit("/", 1)[0]
    times: list[float] = []
    urls: list[str] = []
    pending = None
    duration = 0.0
    map_uri = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            match = _EXTINF_RE.match(line)
            pending = float(match.group(1)) if match else None
            continue
        if line.startswith("#EXT-X-MAP:"):
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                map_uri = match.group(1)
            continue
        if not line or line.startswith("#"):
            continue
        url = line if line.startswith("http") else (
            origin + line if line.startswith("/") else f"{base}/{line}")
        times.append(duration)
        urls.append(url.split("?")[0])
        duration += pending or 0.0
        pending = None
    if map_uri:
        map_url = (map_uri if map_uri.startswith("http") else
                   (origin + map_uri if map_uri.startswith("/")
                    else f"{base}/{map_uri}")).split("?")[0]
        urls.insert(0, map_url)
        times.insert(0, 0.0)
    total_duration = duration + (pending or 0.0)
    return times, urls, round(total_duration, 3)


def model_from_urls(segment_times: list[float], segment_urls: list[str],
                    captured_urls: set[str]) -> CoverageModel:
    """Build a model from a playlist parse plus the captured URL set."""
    model = CoverageModel(segment_times)
    url_to_index = {url: index for index, url in enumerate(segment_urls)}
    for url in captured_urls:
        index = url_to_index.get(url.split("?")[0])
        if index is not None:
            model.mark_captured(index)
    return model
