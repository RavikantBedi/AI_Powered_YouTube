"""Integration test for the render chain. Skipped automatically when FFmpeg is absent.

This exercises the real pipeline code end to end — TTS, measured durations, Ken Burns
motion, concatenation, ducked music mix, subtitle burn-in, and the final mux — because
audio/video synchronization is the one property unit tests cannot prove.

Run with:  pytest tests/test_render_integration.py -v
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from app.agents.base import AgentContext
from app.agents.editor import EditorAgent
from app.agents.storyboard import StoryboardAgent
from app.agents.subtitles import build_cues, render_srt
from app.core.dna import load_channel_dna
from app.providers.tts import EdgeTTS
from app.providers.video import KenBurnsVideoProvider
from app.schemas.core import Script
from app.services import ffmpeg

pytestmark = pytest.mark.skipif(
    not ffmpeg.ffmpeg_available(),
    reason="FFmpeg/ffprobe not on PATH — see docs/DEPLOYMENT.md")

NARRATION = (
    "सत्रह दिसंबर उन्नीस सौ इकहत्तर की रात थी। रावलपिंडी की जेल में सन्नाटा पसरा हुआ था। "
    "तीन भारतीय पायलट उस अंधेरे में जाग रहे थे। उनके पास एक नक्शा था और एक योजना थी। "
    "दीवार के उस पार आज़ादी थी। लेकिन बीच में कांटेदार तार और पहरेदार खड़े थे।"
)


def _write_png(path: Path, w: int = 1536, h: int = 864) -> None:
    """A real gradient PNG so Ken Burns motion has something to move across."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            shade = int(40 + 160 * (x / w) * (1 - y / h))
            raw += bytes((min(90 + shade, 255), min(70 + shade, 255), min(40 + shade, 255)))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b""))


@pytest.mark.asyncio
async def test_full_render_chain_stays_in_sync(store):
    ctx = AgentContext(project_id="test-project", channel_id="anidoc",
                       dna=load_channel_dna("anidoc"), store=store)

    # 1. Split narration into scenes.
    script = Script(text=NARRATION, word_count=len(NARRATION.split()), language="hi-IN")
    board = await StoryboardAgent().execute(ctx, script=script, wpm=130)
    assert len(board.scenes) >= 2

    # 2. Synthesize each scene and take its MEASURED duration as truth.
    tts = EdgeTTS()
    for scene in board.scenes:
        out = store.path(f"audio/segments/{scene.scene_id}.mp3")
        await tts.synthesize(text=scene.narration_text, language="hi-IN",
                             out_path=out, speed=0.95)
        scene.audio_path = str(out)
        scene.duration_seconds = round(await ffmpeg.probe_duration(out), 3)

    clock = 0.0
    for scene in board.scenes:
        scene.start_seconds = round(clock, 3)
        clock += scene.duration_seconds
    board.total_duration = round(clock, 3)

    # 3. Animate stills to exactly those durations.
    kenburns = KenBurnsVideoProvider()
    for i, scene in enumerate(board.scenes):
        image = store.path(f"visuals/{scene.scene_id}.png")
        _write_png(image)
        scene.image_path = str(image)
        scene.motion = ["slow push-in", "gentle pan", "controlled zoom"][i % 3]
        clip = store.path(f"visuals/{scene.scene_id}.mp4")
        await kenburns.animate(image_path=image, motion=scene.motion,
                               duration_seconds=scene.duration_seconds, out_path=clip)
        scene.clip_path = str(clip)

        info = await ffmpeg.video_info(clip)
        # The core invariant: picture length equals narration length, per scene.
        assert abs(info["duration"] - scene.duration_seconds) < 0.35
        assert (info["width"], info["height"]) == (ffmpeg.WIDTH, ffmpeg.HEIGHT)

    # 4. Subtitles derived from the same measured timings.
    srt_path = Path(store.save_text("subtitles/hi.srt", render_srt(build_cues(board))))

    # 5. Music bed, to exercise the sidechain-ducking mix path.
    music = store.path("audio/bed.wav")
    await ffmpeg._run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=110:duration={max(board.total_duration, 5):.1f}",
        "-af", "volume=0.3", str(music)], "test music bed")

    # 6. Full render.
    final = await EditorAgent().execute(ctx, storyboard=board, music_path=music,
                                        subtitle_path=srt_path, watermark="AniDoc")

    info = await ffmpeg.video_info(final)
    assert info["has_audio"], "a silent render ships nothing"
    assert (info["width"], info["height"]) == (1920, 1080)
    assert abs(info["fps"] - 30) < 1
    # End-to-end sync: the finished film matches the narration timeline.
    assert abs(info["duration"] - board.total_duration) < 2.0

    # 7. The measurements the QA agents rely on.
    loudness = await ffmpeg.measure_loudness(final)
    assert abs(loudness["integrated_lufs"] + 16) < 3.0, "audio must be normalized"
    assert loudness["true_peak_db"] <= 0.0, "true peak must not clip"


@pytest.mark.asyncio
async def test_render_without_music_produces_narration_only(store):
    """An empty music library must still yield a normalized audio master."""
    voice = store.path("audio/voice_only.wav")
    await ffmpeg._run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
        "-c:a", "pcm_s16le", str(voice)], "test voice")

    mixed = await ffmpeg.mix_with_music(voice, None, store.path("audio/mixed_none.wav"))
    assert mixed.exists()
    loudness = await ffmpeg.measure_loudness(mixed)
    assert abs(loudness["integrated_lufs"] + 16) < 4.0
