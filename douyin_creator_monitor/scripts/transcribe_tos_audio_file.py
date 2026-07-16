#!/usr/bin/env python3
"""Upload a local audio file to TOS, transcribe it with Volcengine ASR, then clean up."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import volcengine_asr
import volcengine_tos


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", help="Local WAV/MP3/OGG audio file.")
    parser.add_argument("--keep-object", action="store_true", help="Do not delete the TOS object after ASR.")
    parser.add_argument("--json-output", help="Optional path for raw ASR JSON.")
    parser.add_argument("--text-output", help="Optional path for extracted transcript text.")
    parser.add_argument("--upload-json-output", help="Optional path for TOS upload metadata.")
    parser.add_argument("--url-expires", type=int, help="Pre-signed TOS URL expiry in seconds.")
    args = parser.parse_args()

    object_key = ""
    try:
        object_key, audio_url = volcengine_tos.upload_file(Path(args.audio), expires=args.url_expires)
        if args.upload_json_output:
            upload_payload = {"key": object_key, "url": audio_url}
            Path(args.upload_json_output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.upload_json_output).write_text(
                json.dumps(upload_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        response_text = volcengine_asr.call_asr_url(audio_url)
        transcript = volcengine_asr.extract_text(response_text)

        if args.json_output:
            Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_output).write_text(response_text, encoding="utf-8")
        if args.text_output:
            Path(args.text_output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.text_output).write_text(transcript, encoding="utf-8")

        print(transcript)
        return 0
    finally:
        if object_key and not args.keep_object:
            volcengine_tos.delete_object(object_key)


if __name__ == "__main__":
    raise SystemExit(main())
