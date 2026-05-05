from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw

try:
    from moviepy.editor import ColorClip
except Exception:
    ColorClip = None


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="insta_autolayout_demo_"))
    album = root / "album"
    out = root / "out"
    music_dir = root / "music_cache"
    album.mkdir(parents=True, exist_ok=True)
    music_dir.mkdir(parents=True, exist_ok=True)

    create_demo_image(album / "IMG_0001.jpg", (1600, 2000), (201, 112, 54), "Hero")
    create_demo_image(album / "IMG_0002.jpg", (1500, 2000), (44, 112, 182), "Portrait")
    create_demo_image(album / "IMG_0003.jpg", (2000, 1400), (44, 160, 108), "Wide")
    create_demo_image(album / "IMG_0004.jpg", (2000, 1400), (44, 160, 108), "Wide 2")
    create_demo_image(album / "IMG_0005.jpg", (1200, 1200), (118, 74, 170), "Square")
    create_demo_audio(music_dir / "night_drive_128.wav", duration=8.0, bpm=128)

    if ColorClip is not None:
        clip = ColorClip(size=(1080, 1920), color=(232, 180, 82)).set_duration(2.0)
        clip.write_videofile(str(album / "clip_01.mp4"), fps=24, codec="libx264", audio=False)

    cmd = [
        sys.executable,
        "-m",
        "insta_autolayout",
        "--input",
        str(album),
        "--output",
        str(out),
        "--count",
        "1",
        "--style",
        "fast_punchy",
        "--duration-min",
        "6",
        "--duration-max",
        "8",
        "--audio-variants",
        "silent,auto,bpm128",
        "--music-dir",
        str(music_dir),
        "--explain",
    ]
    subprocess.run(cmd, check=True)

    assert (out / "video_01" / "report.json").exists()
    assert (out / "video_01" / "auto_soundtrack" / "final.mp4").exists()
    print(f"Demo run completed in {out}")
    shutil.rmtree(root)


def create_demo_image(path: Path, size: tuple[int, int], color: tuple[int, int, int], label: str) -> None:
    image = Image.new("RGB", size, color=color)
    draw = ImageDraw.Draw(image)
    draw.ellipse((size[0] * 0.25, size[1] * 0.18, size[0] * 0.75, size[1] * 0.78), outline=(255, 255, 255), width=18)
    draw.text((48, 48), label, fill=(255, 255, 255))
    image.save(path, quality=95)


def create_demo_audio(path: Path, duration: float, bpm: int, sample_rate: int = 44100) -> None:
    import math

    samples = int(duration * sample_rate)
    beat_sec = 60.0 / bpm
    frames = bytearray()
    for index in range(samples):
        time = index / sample_rate
        envelope = 0.15
        phase = (time % beat_sec) / beat_sec
        if phase < 0.18:
            envelope += (1.0 - phase / 0.18) * 0.55
        value = envelope * math.sin(2 * math.pi * 110 * time)
        pcm = int(max(-1.0, min(1.0, value)) * 32767)
        frames.extend(pcm.to_bytes(2, "little", signed=True))
        frames.extend(pcm.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(frames))


if __name__ == "__main__":
    main()
