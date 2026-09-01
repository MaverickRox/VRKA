"""Generic assembly of browser-captured HLS media into one output file.

Consumes the capture manifest written by the protected-browser body-capture
layer (see browser_capture) plus the spilled content-keyed object files, and
reconstructs a single media file through two generic modes:

1. playlist mode  - a captured variant playlist lists the segment order;
2. observation-order mode - providers that omit Content-Type or serve
   unreadable variant playlists are reconstructed by grouping captured
   segment objects into per-stream lineages and concatenating in capture
   order.

Pure file/byte logic: no WebView2, no network, fully deterministic.
"""

from __future__ import annotations

import re
from pathlib import Path

from .candidates import _SEGMENT_INIT_RE

PLAYLIST_SUFFIX = ".m3u8"
_SEGMENT_SUFFIXES = (".ts", ".m4s", ".mp4", ".m4a", ".cmfv", ".cmfa", ".aac")
_MEDIA_TYPES = ("mpegurl", "mp2t", "mp4", "video/", "audio/")
_SEGMENT_PATH_RE = re.compile(r"(?:^|[/_.-])(?:seg(?:ment)?|chunk|frag(?:ment)?|part)[-_]?\d+", re.I)
_EXT_X_MAP_RE = re.compile(r"#EXT-X-MAP:URI=\"([^\"]+)\"")


def classify(url: str, content_type: str = "") -> str:
    """Classify one captured response: 'playlist', 'segment', or 'other'.

    Multiple evidence signals: content type when present, URL suffix, and
    the generic segment path shapes used by HLS packagers.  Content-Type is
    never trusted alone (live providers omit it).
    """
    lowered_type = (content_type or "").lower()
    path = (url or "").split("?")[0].lower()
    if "mpegurl" in lowered_type or path.endswith(PLAYLIST_SUFFIX):
        return "playlist"
    if any(marker in lowered_type for marker in ("mp2t", "video/", "audio/")):
        return "segment"
    if path.endswith(_SEGMENT_SUFFIXES):
        return "segment"
    if _SEGMENT_PATH_RE.search(path):
        return "segment"
    return "other"


def _resolve_segment_url(seg: str, playlist_url: str) -> str:
    if seg.startswith("http://") or seg.startswith("https://"):
        return seg
    origin = "/".join(playlist_url.split("/")[:3])
    if seg.startswith("/"):
        return origin + seg
    base = playlist_url.split("?")[0].rsplit("/", 1)[0]
    return f"{base}/{seg}"


def _follow_redirects(url: str, redirect_map: dict[str, str]) -> str:
    path = url.split("?")[0]
    seen = set()
    while path in redirect_map and path not in seen:
        seen.add(path)
        path = redirect_map[path].split("?")[0]
    return path


def _lineage(url: str) -> str:
    """Per-stream directory of a segment URL: everything above the file name.

    CDNs address one rendition's segments from one path prefix; different
    renditions/servers differ in that prefix (observed live: one page
    exposed three lineages concurrently)."""
    return url.split("?")[0].rsplit("/", 1)[0]


def assemble(manifest: list[dict], objects_dir: Path, out_path: Path,
             redirect_map: dict[str, str] | None = None) -> dict:
    """Assemble captured objects into one media file.

    ``manifest`` entries: {url, status, bytes, object, content_type} where
    ``object`` is the content-keyed file name under ``objects_dir``.
    Returns a report dict; ``assembled`` is True only when at least one
    segment object was concatenated.  Missing segments are reported, never
    fabricated.
    """
    redirect_map = redirect_map or {}
    playlists = [
        e for e in manifest
        if e.get("object") and e.get("bytes", 0) > 0
        and classify(e.get("url", ""), e.get("content_type", "")) == "playlist"
    ]
    segments = [
        e for e in manifest
        if e.get("object") and e.get("bytes", 0) > 0
        and classify(e.get("url", ""), e.get("content_type", "")) == "segment"
    ]

    if playlists and segments:
        result = _assemble_from_playlist(
            playlists[-1], manifest, segments, objects_dir, out_path, redirect_map)
        if result.get("assembled"):
            return result

    return _assemble_from_observation_order(segments, objects_dir, out_path)


def _assemble_from_playlist(variant, manifest, segments, objects_dir, out_path,
                            redirect_map) -> dict:
    try:
        text = (objects_dir / variant["object"]).read_text(errors="replace")
    except OSError as exc:
        return {"assembled": False, "mode": "playlist",
                "reason": f"playlist unreadable: {exc}"}
    seg_lines = [line.strip() for line in text.splitlines()
                 if line.strip() and not line.startswith("#")]
    if not seg_lines:
        return {"assembled": False, "mode": "playlist",
                "reason": "playlist lists no segments"}
    # fMP4 playlists declare the init fragment via EXT-X-MAP; it must head
    # the concatenation or the file has no moov and cannot be parsed.
    init_lines = [match.strip() for match in
                  _EXT_X_MAP_RE.findall(text)]
    ordered = init_lines + seg_lines
    parts: list[Path] = []
    missing: list[str] = []
    for line in ordered:
        seg_path = _follow_redirects(_resolve_segment_url(line, variant["url"]),
                                     redirect_map)
        match = next(
            (e for e in reversed(segments)
             if e["url"].split("?")[0] == seg_path), None)
        if match is None:
            missing.append(seg_path)
            continue
        parts.append(objects_dir / match["object"])
    if not parts:
        return {"assembled": False, "mode": "playlist",
                "reason": "no listed segment was captured",
                "playlist_segments": len(seg_lines), "missing": missing[:5]}
    _concat(parts, out_path)
    return {"assembled": True, "mode": "playlist", "segments": len(parts),
            "playlist_segments": len(seg_lines), "missing": missing[:5],
            "bytes": out_path.stat().st_size}


def _assemble_from_observation_order(segments, objects_dir, out_path) -> dict:
    if not segments:
        return {"assembled": False, "mode": "observation-order",
                "reason": "no media segments were captured"}
    groups: dict[str, list[dict]] = {}
    for entry in segments:
        groups.setdefault(_lineage(entry["url"]), []).append(entry)
    # The richest lineage wins; ties break on the earliest first capture so
    # repeated server probes do not outrank the stream actually playing.
    best_lineage = max(
        sorted(groups),
        key=lambda name: (sum(e["bytes"] for e in groups[name]),
                          -min(e.get("seq", 0) for e in groups[name])),
    )
    chosen = groups[best_lineage]
    # fMP4 ordering: the init fragment (declared moov) must head the file.
    # When the player's reload served the init from cache (no response
    # event), rescue a same-stream init from another captured lineage.
    inits = [e for e in chosen
             if _SEGMENT_INIT_RE.search(e["url"].split("?")[0])]
    fragments = [e for e in chosen if e not in inits]
    if not inits:
        stream_id = best_lineage.rsplit("/", 1)[-1]
        for lineage, entries in groups.items():
            if lineage == best_lineage:
                continue
            for entry in entries:
                if (stream_id and stream_id in entry["url"]
                        and _SEGMENT_INIT_RE.search(entry["url"].split("?")[0])):
                    inits.append(entry)
                    break
            if inits:
                break
    ordered = inits + fragments
    with open(out_path, "wb") as handle:
        for entry in ordered:
            handle.write((objects_dir / entry["object"]).read_bytes())
    lineage_counts = {
        name: {"segments": len(entries), "bytes": sum(e["bytes"] for e in entries)}
        for name, entries in sorted(groups.items())
    }
    return {"assembled": True, "mode": "observation-order",
            "segments": len(fragments), "inits": len(inits),
            "lineage_counts": lineage_counts,
            "bytes": out_path.stat().st_size}


def _concat(parts: list[Path], out_path: Path) -> None:
    with open(out_path, "wb") as handle:
        for part in parts:
            handle.write(part.read_bytes())


_EXTINF_RE = re.compile(r"#EXTINF:([\d.]+)")


def playlist_coverage(manifest: list[dict], objects_dir: Path) -> dict:
    """Authoritative VOD coverage from a captured playlist.

    Matches the playlist's segment URIs against the captured object URLs
    (path-only; signed query strings differ per request).  Returns the
    expected segment list with per-segment media times, the captured set,
    the first missing index, and its media time - everything a coverage
    controller needs to seek to the next gap.  No ffprobe involvement.
    """
    playlists = [
        e for e in manifest
        if e.get("object") and e.get("bytes", 0) > 0
        and classify(e.get("url", ""), e.get("content_type", "")) == "playlist"
    ]
    # The variant (longest playlist with EXTINF entries) is authoritative.
    best = None
    for entry in playlists:
        try:
            text = (objects_dir / entry["object"]).read_text(errors="replace")
        except OSError:
            continue
        if "#EXTINF" not in text:
            continue
        if best is None or len(text) > best[1]:
            best = (entry, len(text), text)
    if best is None:
        return {"known": False, "reason": "no readable media playlist captured"}
    entry, _, text = best
    duration = 0.0
    expected: list[dict] = []
    pending_duration = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                pending_duration = float(_EXTINF_RE.match(line).group(1))
            except (AttributeError, ValueError):
                pending_duration = None
            continue
        if not line or line.startswith("#"):
            continue
        expected.append({
            "url": _resolve_segment_url(line, entry["url"]).split("?")[0],
            "time": round(duration, 3),
            "duration": pending_duration or 0.0,
        })
        duration += pending_duration or 0.0
    captured_paths = {
        e["url"].split("?")[0] for e in manifest
        if e.get("object") and e.get("bytes", 0) > 0
        and classify(e.get("url", ""), e.get("content_type", "")) == "segment"
    }
    captured_indices = [i for i, seg in enumerate(expected)
                        if seg["url"] in captured_paths]
    missing_indices = [i for i in range(len(expected))
                       if i not in set(captured_indices)]
    first_missing = missing_indices[0] if missing_indices else None
    return {
        "known": True,
        "playlist_url": entry["url"].split("?")[0],
        "total_segments": len(expected),
        "captured_segments": len(captured_indices),
        "coverage": round(len(captured_indices) / len(expected), 4) if expected else 0.0,
        "first_missing_index": first_missing,
        "first_missing_time": (expected[first_missing]["time"]
                               if first_missing is not None else None),
        "total_duration": round(duration, 3),
        "segment_times": [seg["time"] for seg in expected],
        "discontinuities": "#EXT-X-DISCONTINUITY" in text,
    }
