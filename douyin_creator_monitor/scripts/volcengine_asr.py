#!/usr/bin/env python3
"""Call Volcengine ASR with a local audio file.

Credentials are read from environment variables so secrets are not committed:

- VOLC_ASR_APP_ID
- VOLC_ASR_ACCESS_TOKEN
- VOLC_ASR_CLUSTER
- VOLC_ASR_ENDPOINT, optional

The default request shape targets the common ByteDance/Volcengine OpenSpeech
HTTP JSON style. If the account is enabled for a different ASR product, keep
this script as the single integration point and adjust build_payload().
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v1/asr"


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def build_payload(audio_path: Path, app_id: str, token: str, cluster: str) -> dict:
    audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    return {
        "app": {
            "appid": app_id,
            "token": token,
            "cluster": cluster,
        },
        "user": {
            "uid": "douyin_creator_monitor",
        },
        "audio": {
            "format": audio_path.suffix.lower().lstrip(".") or "wav",
            "rate": 16000,
            "bits": 16,
            "channel": 1,
            "language": "zh-CN",
            "data": audio_b64,
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "sequence": 1,
            "nbest": 1,
            "workflow": "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuate",
            "show_utterances": True,
            "result_type": "single",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", help="Path to WAV/M4A audio.")
    parser.add_argument("--output", help="Where to write the raw ASR JSON.")
    parser.add_argument("--endpoint", default=os.environ.get("VOLC_ASR_ENDPOINT", DEFAULT_ENDPOINT))
    args = parser.parse_args()

    audio_path = Path(args.audio)
    payload = build_payload(
        audio_path=audio_path,
        app_id=env("VOLC_ASR_APP_ID"),
        token=env("VOLC_ASR_ACCESS_TOKEN"),
        cluster=env("VOLC_ASR_CLUSTER"),
    )
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        args.endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {payload['app']['token']}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            response_text = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {response_text}") from exc

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(response_text, encoding="utf-8")
    print(response_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

