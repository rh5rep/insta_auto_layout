from __future__ import annotations

try:  # MoviePy 2.x
    from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips
except Exception:  # pragma: no cover - fallback for older installs
    try:
        from moviepy.editor import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips
    except Exception:  # pragma: no cover - environment-dependent
        AudioFileClip = None
        ColorClip = None
        CompositeVideoClip = None
        ImageClip = None
        VideoFileClip = None
        concatenate_videoclips = None


__all__ = [
    "AudioFileClip",
    "ColorClip",
    "CompositeVideoClip",
    "ImageClip",
    "VideoFileClip",
    "concatenate_videoclips",
]
