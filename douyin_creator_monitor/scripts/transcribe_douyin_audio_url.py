#!/usr/bin/env python3
"""Transcribe a signed Douyin audio URL without keeping media files.

The audio is downloaded into a temporary directory, converted to 16 kHz mono
WAV for ASR, sent to Volcengine, and then removed automatically. The only
optional outputs are transcript text and raw ASR JSON.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from volcengine_asr import call_asr, extract_text


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-url", required=True, help="Signed Douyin audio URL from the browser session.")
    parser.add_argument("--ffmpeg", required=True, help="Path to ffmpeg.exe.")
    parser.add_argument("--json-output", help="Optional path for raw Volcengine ASR JSON.")
    parser.add_argument("--text-output", help="Optional path for extracted transcript text.")
    parser.add_argument("--endpoint", help="Override Volcengine ASR endpoint.")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="douyin-asr-") as tmp:
        tmp_dir = Path(tmp)
        audio_path = tmp_dir / "source.m4a"
        wav_path = tmp_dir / "source.16k.wav"

        download(args.audio_url, audio_path)
        run_ffmpeg(Path(args.ffmpeg), audio_path, wav_path)
        response_text = call_asr(wav_path, endpoint=args.endpoint)
        transcript = extract_text(response_text)

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
