#!/usr/bin/env python3
"""Transcribe a signed Douyin audio URL without keeping media files.

The default strategy asks Alibaba Cloud Bailian/DashScope Paraformer to read
the audio URL directly. If that fails, the script can download the audio into
a temporary directory and call a configured upload command to produce a
temporary public URL for Paraformer. The only optional outputs are transcript
text and raw ASR JSON.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from bailian_paraformer import AsrRequestError, call_asr_url, extract_text


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


def upload_with_command(upload_command: str, file_path: Path) -> str:
    if "{file}" not in upload_command:
        raise AsrRequestError("Upload command must contain a {file} placeholder.")
    command = upload_command.replace("{file}", str(file_path))
    completed = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
    uploaded_url = completed.stdout.strip().splitlines()[-1].strip() if completed.stdout.strip() else ""
    if not uploaded_url.startswith(("http://", "https://")):
        raise AsrRequestError(f"Upload command did not print a public URL: {uploaded_url}")
    return uploaded_url


def transcribe_via_download(
    audio_url: str,
    ffmpeg: Path,
    endpoint: str | None,
    upload_command: str,
    model: str | None,
) -> str:
    if not str(ffmpeg):
        raise AsrRequestError("FFmpeg path is required for download-upload fallback.")
    if not upload_command:
        raise AsrRequestError(
            "Direct URL transcription failed and no fallback upload command is configured. "
            "Set BAILIAN_UPLOAD_COMMAND or pass --fallback-upload-command. "
            "The command must print a temporary public audio URL."
        )
    with tempfile.TemporaryDirectory(prefix="douyin-asr-") as tmp:
        tmp_dir = Path(tmp)
        audio_path = tmp_dir / "source.m4a"
        wav_path = tmp_dir / "source.16k.wav"

        download(audio_url, audio_path)
        run_ffmpeg(ffmpeg, audio_path, wav_path)
        uploaded_url = upload_with_command(upload_command, wav_path)
        return call_asr_url(uploaded_url, endpoint=endpoint, model=model)


def transcribe(
    audio_url: str,
    ffmpeg: Path,
    endpoint: str | None,
    mode: str,
    upload_command: str,
    model: str | None,
) -> tuple[str, str]:
    if mode == "direct-url":
        return call_asr_url(audio_url, endpoint=endpoint, model=model), "direct-url"

    if mode == "download-upload":
        return transcribe_via_download(audio_url, ffmpeg, endpoint, upload_command, model), "download-upload"

    try:
        return call_asr_url(audio_url, endpoint=endpoint, model=model), "direct-url"
    except AsrRequestError as exc:
        print(f"Direct URL transcription failed, falling back to temporary download: {exc}", file=sys.stderr)
        return transcribe_via_download(audio_url, ffmpeg, endpoint, upload_command, model), "download-upload"


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
    parser.add_argument("--fallback-upload-command", default=os.environ.get("BAILIAN_UPLOAD_COMMAND", ""))
    parser.add_argument("--model", default=os.environ.get("BAILIAN_ASR_MODEL", "paraformer-v2"))
    parser.add_argument("--json-output", help="Optional path for raw Bailian/DashScope ASR JSON.")
    parser.add_argument("--text-output", help="Optional path for extracted transcript text.")
    parser.add_argument("--endpoint", help="Override Bailian/DashScope ASR endpoint.")
    args = parser.parse_args()

    try:
        response_text, strategy = transcribe(
            audio_url=args.audio_url,
            ffmpeg=Path(args.ffmpeg),
            endpoint=args.endpoint,
            mode=args.mode,
            upload_command=args.fallback_upload_command,
            model=args.model,
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
