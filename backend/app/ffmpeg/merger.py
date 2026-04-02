from pathlib import Path


class FFmpegMergeService:
    def build_copy_merge_command(
        self, video_path: Path, audio_path: Path, output_path: Path
    ) -> list[str]:
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-c",
            "copy",
            str(output_path),
        ]

