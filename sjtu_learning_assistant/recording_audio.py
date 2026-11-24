"""Bounded persistent audio preparation; FFmpeg never receives signed URLs.

Download only direct school-host MP4s without redirects, then force local MOV
with file-only protocols. Neither playlists nor external media references run.
"""
from __future__ import annotations
import hashlib
import math
import os
import re
import stat
import subprocess
import time
from uuid import uuid4
from pathlib import Path
from urllib.parse import unquote, urlsplit
import httpx
from sjtu_learning_assistant import media_features
from sjtu_learning_assistant.video_service import PLAYBACK_HOSTS

MAX_CHUNK_BYTES = 50_000_000
MAX_CHUNK_SECONDS = 3600
MAX_SOURCE_BYTES = 2_000_000_000
MAX_AUDIO_BYTES = 512_000_000
MAX_SOURCE_SECONDS = 6 * 3600

class RecordingAudioError(RuntimeError):
    """Safe public messages: no paths, URLs or subprocess/upstream output."""

def ffmpeg_executable() -> str:
    executable = media_features._ffmpeg_executable()
    if not executable:
        raise RecordingAudioError("packaged_ffmpeg_unavailable")
    return executable

def validate_media_url(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 8192:
        raise RecordingAudioError("unsafe_playback_url")
    try:
        parsed = urlsplit(value)
        path = unquote(parsed.path)
        valid = (parsed.scheme == "https" and parsed.hostname in PLAYBACK_HOSTS
                 and parsed.port in (None, 443) and parsed.username is None
                 and parsed.password is None and not parsed.fragment
                 and path.lower().endswith(".mp4") and "\\" not in value
                 and not any(ord(c) < 32 or ord(c) == 127 for c in value + path))
    except ValueError:
        valid = False
    if not valid:
        raise RecordingAudioError("unsafe_playback_url")
    return value

def file_metadata(path: Path, duration_seconds: float, *, max_bytes=MAX_AUDIO_BYTES,
                  max_seconds=MAX_SOURCE_SECONDS) -> dict:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RecordingAudioError("unsafe_audio_file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= max_bytes:
            raise RecordingAudioError("audio_file_size_limit")
        duration = float(duration_seconds)
        if not math.isfinite(duration) or not 0 < duration <= max_seconds:
            raise RecordingAudioError("audio_duration_limit")
        digest = hashlib.sha256()
        while data := stream.read(1024 * 1024):
            digest.update(data)
    return {"file": path.name, "bytes": info.st_size, "duration_seconds": duration,
            "sha256": digest.hexdigest()}

def _run(arguments: list[str], *, timeout=1800):
    try:
        return subprocess.run([ffmpeg_executable(), "-nostdin", "-hide_banner",
                               "-max_alloc", "67108864", *arguments],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        raise RecordingAudioError("ffmpeg_failed_or_timed_out") from None

def probe_audio(path: Path) -> tuple[float, str]:
    file_metadata(path, 1, max_bytes=MAX_SOURCE_BYTES)
    result = _run(["-protocol_whitelist", "file", "-f", "mov", "-enable_drefs", "0",
                   "-use_absolute_path", "0", "-i", str(path)], timeout=60)
    output = result.stderr.decode("utf-8", errors="replace")
    found = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    audio = re.search(r"Audio:\s*([a-zA-Z0-9_]+)", output)
    if not found or not audio:
        raise RecordingAudioError("recording_has_no_valid_audio")
    duration = int(found[1]) * 3600 + int(found[2]) * 60 + float(found[3])
    if not 0 < duration <= MAX_SOURCE_SECONDS:
        raise RecordingAudioError("recording_duration_limit")
    return duration, audio[1]

def _download(url: str, destination: Path) -> None:
    validate_media_url(url)
    temporary = destination.with_suffix(".download")
    if temporary.exists() or temporary.is_symlink() or destination.exists() or destination.is_symlink():
        raise RecordingAudioError("unsafe_or_existing_video_destination")
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=20), follow_redirects=False,
                          trust_env=False) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise RecordingAudioError("playback_download_http_error")
                declared = response.headers.get("content-length")
                if declared and (not declared.isdecimal() or not 0 < int(declared) <= MAX_SOURCE_BYTES):
                    raise RecordingAudioError("recording_source_size_limit")
                count = 0
                started = time.monotonic()
                with temporary.open("xb") as stream:
                    os.chmod(temporary, 0o600)
                    for data in response.iter_bytes(1024 * 1024):
                        count += len(data)
                        if count > MAX_SOURCE_BYTES or time.monotonic() - started > 1800:
                            raise RecordingAudioError("recording_source_size_limit")
                        stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not count or (declared and count != int(declared)):
                    raise RecordingAudioError("incomplete_recording_download")
        with temporary.open("rb") as stream:
            magic = stream.read(12)
        if len(magic) < 12 or magic[4:8] != b"ftyp":
            raise RecordingAudioError("recording_not_mp4")
        os.replace(temporary, destination)
    except httpx.HTTPError:
        raise RecordingAudioError("playback_download_failed") from None
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()

def _convert(source: Path, destination: Path, *, copy=False, start=None, seconds=None, expected_duration=None):
    if destination.exists() or destination.is_symlink():
        raise RecordingAudioError("audio_destination_exists")
    # Retain failed conversions; a local retry gets a fresh filename.
    temporary = destination.with_name(destination.stem + ".preparing-" + uuid4().hex + ".m4a")
    arguments = ["-loglevel", "error", "-protocol_whitelist", "file", "-f", "mov",
                 "-enable_drefs", "0", "-use_absolute_path", "0"]
    if start is not None:
        arguments += ["-ss", str(start)]
    arguments += ["-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1"]
    if seconds is not None:
        arguments += ["-t", str(seconds)]
    arguments += (["-c:a", "copy"] if copy else ["-c:a", "aac", "-ac", "1", "-ar", "16000", "-b:a", "64000"])
    arguments += ["-threads", "1", "-fs", str(MAX_AUDIO_BYTES), "-f", "ipod", str(temporary)]
    result = _run(arguments)
    if result.returncode != 0:
        raise RecordingAudioError("audio_extraction_failed")
    duration, _ = probe_audio(temporary)
    file_metadata(temporary, duration)
    if expected_duration is not None and abs(duration - expected_duration) > 2:
        raise RecordingAudioError("audio_duration_mismatch")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return duration

def prepare_recording_audio(playback: dict, directory: Path) -> dict:
    """Persist source and chunks. AAC sources are copied, not reencoded.

    Above either cap use conservative 3500-second 64-kbps AAC chunks. Validate
    every chunk against both caps; source/duration/errors never bypass caps.
    """
    source = directory / "source-audio.m4a"
    if source.exists():
        duration, _ = probe_audio(source)
        audio = file_metadata(source, duration)
    else:
        values = playback.get("urls") or [playback.get("url")]
        if not isinstance(values, (list, tuple)) or len(values) > 8:
            raise RecordingAudioError("playback_variant_limit")
        values = list(dict.fromkeys(validate_media_url(value) for value in values))
        if not values:
            raise RecordingAudioError("no_playback_sources")
        last_error = RecordingAudioError("no_usable_playback_audio")
        for index, url in enumerate(values):
            video = directory / f"source-video-{index:02d}.mp4"
            try:
                if not video.exists():
                    _download(url, video)
                if video.is_symlink() or not 0 < video.stat().st_size <= MAX_SOURCE_BYTES:
                    raise RecordingAudioError("unsafe_source_video")
                expected, codec = probe_audio(video)
                actual = _convert(video, source, copy=codec == "aac", expected_duration=expected)
                if abs(actual - expected) > 2:
                    raise RecordingAudioError("audio_duration_mismatch")
                audio = file_metadata(source, actual)
                audio["copy_original_aac"] = codec == "aac"
                break
            except RecordingAudioError as exc:
                last_error = exc
                if source.exists():
                    raise
        else:
            raise last_error
    if audio["bytes"] <= MAX_CHUNK_BYTES and audio["duration_seconds"] <= MAX_CHUNK_SECONDS:
        return {"audio": audio, "chunks": [dict(audio)]}
    chunks = []
    duration = audio["duration_seconds"]
    count = math.ceil(duration / 3500)
    if count > 8:
        raise RecordingAudioError("recording_chunk_limit")
    for index in range(count):
        start = index * 3500
        expected = min(3500, duration - start)
        path = directory / f"chunk-{index:03d}.m4a"
        actual = probe_audio(path)[0] if path.exists() else _convert(source, path, start=start, seconds=expected, expected_duration=expected)
        if abs(actual - expected) > 2:
            raise RecordingAudioError("audio_chunk_duration_mismatch")
        metadata = file_metadata(path, actual, max_bytes=MAX_CHUNK_BYTES, max_seconds=MAX_CHUNK_SECONDS)
        metadata["index"] = index
        chunks.append(metadata)
    return {"audio": audio, "chunks": chunks}
