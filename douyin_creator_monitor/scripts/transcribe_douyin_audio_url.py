#!/usr/bin/env python3
"""Transcribe a signed Douyin audio URL without keeping media files.

The default strategy asks Volcengine to read the audio URL directly. If that
fails, the script downloads the audio into a temporary directory, converts it
to 16 kHz mono WAV, uploads it to Volcengine, and removes the temporary files.
The only optional outputs are transcript text and raw ASR JSON.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from volcengine_asr import AsrRequestError, call_asr, call_asr_url, extract_text


def download(url: str, output: Path) -> None:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
            )
        },
    )
    with urlopen(request, timeout=60) as response:
        output.write_bytes(response.read())


def run_ffmpeg(ffmpeg: Path, source: Path, wav_path: Path) -> None:
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
            "-y",
        ],
        check=True,
    )


def transcribe_via_download(audio_url: str, ffmpeg: Path, endpoint: str | None) -> str:
    if not str(ffmpeg):
        raise AsrRequestError("FFmpeg path is required for download-upload fallback.")
    with tempfile.TemporaryDirectory(prefix="douyin-asr-") as tmp:
        tmp_dir = Path(tmp)
        audio_path = tmp_dir / "source.m4a"
        wav_path = tmp_dir / "source.16k.wav"

        download(audio_url, audio_path)
        run_ffmpeg(ffmpeg, audio_path, wav_path)
        return call_asr(wav_path, endpoint=endpoint)


def transcribe(audio_url: str, ffmpeg: Path, endpoint: str | None, mode: str) -> tuple[str, str]:
    if mode == "direct-url":
        return call_asr_url(audio_url, endpoint=endpoint), "direct-url"

    if mode == "download-upload":
        return transcribe_via_download(audio_url, ffmpeg, endpoint), "download-upload"

    try:
        return call_asr_url(audio_url, endpoint=endpoint), "direct-url"
    except AsrRequestError as exc:
        print(f"Direct URL transcription failed, falling back to temporary download: {exc}", file=sys.stderr)
        return transcribe_via_download(audio_url, ffmpeg, endpoint), "download-upload"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-url", required=True, help="Signed Douyin audio URL from the browser session.")
    parser.add_argument(
        "--ffmpeg",
        default=os.environ.get("FFMPEG_PATH", ""),
        help="Path to ffmpeg.exe. Required only when download-upload fallback is used.",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "direct-url", "download-upload"],
        default="auto",
        help="auto tries direct URL first, then falls back to temporary download/upload.",
    )
    parser.add_argument("--json-output", help="Optional path for raw Volcengine ASR JSON.")
    parser.add_argument("--text-output", help="Optional path for extracted transcript text.")
    parser.add_argument("--endpoint", help="Override Volcengine ASR endpoint.")
    args = parser.parse_args()

    try:
        response_text, strategy = transcribe(
            audio_url=args.audio_url,
            ffmpeg=Path(args.ffmpeg),
            endpoint=args.endpoint,
            mode=args.mode,
        )
    except AsrRequestError as exc:
        raise SystemExit(str(exc)) from exc

    transcript = extract_text(response_text)
    print(f"strategy={strategy}", file=sys.stderr)

    if args.json_output:
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_output).write_text(response_text, encoding="utf-8")
    if args.text_output:
        Path(args.text_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.text_output).write_text(transcript, encoding="utf-8")

    print(transcript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
