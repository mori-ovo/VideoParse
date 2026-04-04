import shutil
from pathlib import Path

from app.core.config import settings
from app.services.downloader_service import MediaTarget


class FFmpegMergeService:
    def build_copy_merge_command(
        self,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
    ) -> list[str]:
        return [
            self.resolve_ffmpeg_binary(),
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-c",
            "copy",
            str(output_path),
        ]

    def build_stream_merge_command(
        self,
        video_target: MediaTarget,
        audio_target: MediaTarget,
    ) -> list[str]:
        # 直接把上下游音视频流 copy 成一个可播放输出，避免先完整落盘再合流。
        command = [
            self.resolve_ffmpeg_binary(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        command.extend(self._build_input_args(video_target))
        command.extend(self._build_input_args(audio_target))
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c",
                "copy",
                # 片段化 mp4 能让播放器更早拿到 moov/moof，适合边合流边播放。
                "-movflags",
                "frag_keyframe+empty_moov+default_base_moof",
                "-f",
                settings.merge_output_format,
                "pipe:1",
            ]
        )
        return command

    def resolve_ffmpeg_binary(self) -> str:
        if settings.ffmpeg_location:
            configured_path = Path(settings.ffmpeg_location)
            if configured_path.exists():
                return str(configured_path)
        return shutil.which("ffmpeg") or "ffmpeg"

    def _build_input_args(self, target: MediaTarget) -> list[str]:
        # 上游视频站偶尔会断流，这里让 ffmpeg 尽量自动重连。
        args = [
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_at_eof",
            "1",
        ]
        header_block = self._serialize_headers(target.headers)
        if header_block:
            args.extend(["-headers", header_block])
        args.extend(["-i", target.url])
        return args

    def _serialize_headers(self, headers: dict[str, str]) -> str | None:
        filtered = [
            f"{key}: {value}"
            for key, value in headers.items()
            if value and key.lower() != "accept-encoding"
        ]
        if not filtered:
            return None
        return "\r\n".join(filtered) + "\r\n"


ffmpeg_merge_service = FFmpegMergeService()
