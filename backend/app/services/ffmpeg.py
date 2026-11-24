"""Programmatic render engine built on FFmpeg — the replacement for manual editing.

Everything the Editor Agent needs: Ken-Burns motion from stills, probing durations,
concatenation, audio mixing with sidechain ducking, subtitle burn-in, loudness
normalization, and final H.264/AAC muxing.

All calls are async subprocesses; failures raise RenderError with the tail of stderr.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from app.core.errors import AgencyError

log = logging.getLogger("agency.ffmpeg")

WIDTH, HEIGHT, FPS = 1920, 1080, 30


class RenderError(AgencyError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def _run(args: list[str], what: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-1500:]
        raise RenderError(f"{what} failed (exit {proc.returncode}): {tail}")


async def probe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RenderError(f"ffprobe failed for {path.name}: {err.decode(errors='replace')[-500:]}")
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise RenderError(f"could not read duration of {path.name}") from e


# ── Motion (Ken Burns) ──────────────────────────────────────────────────

def _zoompan_filter(motion: str, duration: float) -> str:
    """Documentary-grade subtle motion. Frame count drives the zoom/pan ramp."""
    frames = max(int(duration * FPS), 1)
    # Oversample to 2x before zoompan to avoid the shaky-pixel artifact zoompan
    # produces when panning a source at output resolution.
    pre = f"scale={WIDTH*2}:{HEIGHT*2}:flags=lanczos"
    post = f"scale={WIDTH}:{HEIGHT}:flags=lanczos,setsar=1,fps={FPS},format=yuv420p"
    m = (motion or "").lower()
    if "push" in m or "zoom in" in m:
        zp = f"zoompan=z='min(1.0+0.0009*on,1.12)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    elif "pull" in m or "zoom out" in m:
        zp = f"zoompan=z='max(1.12-0.0009*on,1.0)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    elif "pan left" in m:
        zp = (f"zoompan=z=1.10:d={frames}:x='(iw-iw/zoom)*(1-on/{frames})':"
              f"y='ih/2-(ih/zoom/2)'")
    elif "pan" in m:  # default pan direction: right
        zp = f"zoompan=z=1.10:d={frames}:x='(iw-iw/zoom)*(on/{frames})':y='ih/2-(ih/zoom/2)'"
    else:  # static hold with a barely-perceptible drift, never a frozen frame
        zp = f"zoompan=z='min(1.0+0.0004*on,1.05)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    return f"{pre},{zp}:s={WIDTH}x{HEIGHT}:fps={FPS},{post}"


async def ken_burns_clip(image_path: Path, out_path: Path, duration: float, motion: str) -> Path:
    """Turn a still into a moving clip. This is the always-available motion engine."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
        "-t", f"{duration:.3f}",
        "-vf", _zoompan_filter(motion, duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-an", str(out_path),
    ], f"ken-burns {image_path.name}")
    return out_path


async def normalize_clip(src: Path, out_path: Path, duration: float) -> Path:
    """Conform an externally generated clip (provider motion) to the timeline spec."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "ffmpeg", "-y", "-i", str(src), "-t", f"{duration:.3f}",
        "-vf", (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p"),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-an", str(out_path),
    ], f"normalize {src.name}")
    return out_path


# ── Assembly ────────────────────────────────────────────────────────────

async def concat_clips(clips: list[Path], out_path: Path, crossfade: float = 0.0) -> Path:
    """Concatenate clips. crossfade > 0 applies an xfade chain between every pair."""
    if not clips:
        raise RenderError("no clips to concatenate")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if crossfade <= 0 or len(clips) == 1:
        listfile = out_path.parent / f"{out_path.stem}_concat.txt"
        listfile.write_text(
            "".join(f"file '{p.resolve().as_posix()}'\n" for p in clips), encoding="utf-8")
        await _run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-r", str(FPS), str(out_path),
        ], "concat")
        listfile.unlink(missing_ok=True)
        return out_path

    durations = [await probe_duration(c) for c in clips]
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    parts, offset, prev = [], 0.0, "[0:v]"
    for i in range(1, len(clips)):
        offset += durations[i - 1] - crossfade
        label = f"[x{i}]"
        parts.append(f"{prev}[{i}:v]xfade=transition=fade:duration={crossfade}:"
                     f"offset={offset:.3f}{label}")
        prev = label
    await _run([
        "ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts), "-map", prev,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", str(FPS), str(out_path),
    ], "concat-xfade")
    return out_path


async def concat_audio(segments: list[Path], out_path: Path) -> Path:
    if not segments:
        raise RenderError("no audio segments to concatenate")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for s in segments:
        inputs += ["-i", str(s)]
    filt = "".join(f"[{i}:a]" for i in range(len(segments))) + \
           f"concat=n={len(segments)}:v=0:a=1[out]"
    await _run([
        "ffmpeg", "-y", *inputs, "-filter_complex", filt, "-map", "[out]",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out_path),
    ], "concat-audio")
    return out_path


async def silence(duration: float, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{duration:.3f}", "-c:a", "pcm_s16le", str(out_path),
    ], "silence")
    return out_path


async def mix_with_music(voice: Path, music: Path | None, out_path: Path,
                         music_gain_db: float = -22.0) -> Path:
    """Mix narration with background music using sidechain ducking so speech always wins."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if music is None:
        await _run([
            "ffmpeg", "-y", "-i", str(voice),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out_path),
        ], "normalize-voice")
        return out_path
    voice_dur = await probe_duration(voice)
    filt = (
        f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{voice_dur:.3f},"
        f"volume={music_gain_db}dB,afade=t=out:st={max(voice_dur - 3, 0):.3f}:d=3[bed];"
        "[0:a]asplit=2[v1][sc];"
        "[bed][sc]sidechaincompress=threshold=0.02:ratio=12:attack=15:release=350[ducked];"
        "[v1][ducked]amix=inputs=2:duration=first:dropout_transition=0,"
        "loudnorm=I=-16:TP=-1.5:LRA=11[out]"
    )
    await _run([
        "ffmpeg", "-y", "-i", str(voice), "-i", str(music),
        "-filter_complex", filt, "-map", "[out]",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out_path),
    ], "mix-music")
    return out_path


async def mux(video: Path, audio: Path, out_path: Path,
              subtitles: Path | None = None, watermark_text: str = "") -> Path:
    """Final mux: H.264 + AAC, optional burned-in subtitles and watermark."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    filters: list[str] = []
    if subtitles is not None:
        # FFmpeg filter args need escaped path separators and colons on Windows.
        sub = str(subtitles.resolve()).replace("\\", "/").replace(":", "\\:")
        filters.append(
            f"subtitles='{sub}':force_style='FontName=Noto Sans Devanagari,FontSize=20,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H90000000,BorderStyle=3,Outline=2,"
            f"Shadow=0,MarginV=48'")
    if watermark_text:
        safe = watermark_text.replace(":", "\\:").replace("'", "")
        filters.append(f"drawtext=text='{safe}':fontcolor=white@0.55:fontsize=26:x=w-tw-32:y=32")
    args = ["ffmpeg", "-y", "-i", str(video), "-i", str(audio)]
    if filters:
        args += ["-vf", ",".join(filters)]
    args += [
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest", str(out_path),
    ]
    await _run(args, "final mux")
    return out_path


async def extract_frame(video: Path, at_seconds: float, out_path: Path) -> Path:
    """Pull a still for the Final QA agent to inspect visually."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "ffmpeg", "-y", "-ss", f"{at_seconds:.3f}", "-i", str(video),
        "-frames:v", "1", "-q:v", "3", str(out_path),
    ], "extract-frame")
    return out_path


async def measure_loudness(audio: Path) -> dict:
    """Return integrated loudness / true peak for the Audio QA agent."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", str(audio), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    text = stderr.decode(errors="replace")
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise RenderError("loudness measurement produced no JSON")
    data = json.loads(text[start:end + 1])
    return {
        "integrated_lufs": float(data.get("input_i", 0)),
        "true_peak_db": float(data.get("input_tp", 0)),
        "lra": float(data.get("input_lra", 0)),
    }


async def video_info(path: Path) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries",
        "stream=width,height,avg_frame_rate,codec_name,codec_type", "-show_entries",
        "format=duration,size", "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RenderError(f"ffprobe failed: {err.decode(errors='replace')[-400:]}")
    data = json.loads(out)
    info = {"duration": float(data.get("format", {}).get("duration", 0)),
            "size_bytes": int(data.get("format", {}).get("size", 0)),
            "width": 0, "height": 0, "fps": 0.0, "has_audio": False}
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not info["width"]:
            info["width"] = stream.get("width", 0)
            info["height"] = stream.get("height", 0)
            rate = stream.get("avg_frame_rate", "0/1")
            num, _, den = rate.partition("/")
            info["fps"] = round(float(num) / float(den), 2) if den and float(den) else 0.0
        elif stream.get("codec_type") == "audio":
            info["has_audio"] = True
    return info
