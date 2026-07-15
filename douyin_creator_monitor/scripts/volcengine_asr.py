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
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v1/asr"
SUCCESS_CODES = {0, 1000, 20000000}


class AsrRequestError(RuntimeError):
    """Raised when Volcengine returns an HTTP or ASR-level failure."""


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def build_base_payload(app_id: str, token: str, cluster: str) -> dict:
    return {
        "app": {
            "appid": app_id,
            "token": token,
            "cluster": cluster,
        },
        "user": {
            "uid": "douyin_creator_monitor",
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


def build_payload(audio_path: Path, app_id: str, token: str, cluster: str) -> dict:
    payload = build_base_payload(app_id, token, cluster)
    audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    payload["audio"] = {
        "format": audio_path.suffix.lower().lstrip(".") or "wav",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
        "language": "zh-CN",
        "data": audio_b64,
    }
    return payload


def infer_url_format(audio_url: str) -> str:
    suffix = Path(urlparse(audio_url).path).suffix.lower().lstrip(".")
    return suffix or "m4a"


def build_url_payload(audio_url: str, app_id: str, token: str, cluster: str) -> dict:
    payload = build_base_payload(app_id, token, cluster)
    payload["audio"] = {
        "format": infer_url_format(audio_url),
        "language": "zh-CN",
        "url": audio_url,
    }
    return payload


def post_payload(payload: dict, endpoint: str | None = None) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        endpoint or os.environ.get("VOLC_ASR_ENDPOINT", DEFAULT_ENDPOINT),
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
        raise AsrRequestError(f"HTTP {exc.code}: {response_text}") from exc

    ensure_success_response(response_text)
    return response_text


def ensure_success_response(response_text: str) -> None:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return

    if not isinstance(payload, dict):
        return

    code = payload.get("code", payload.get("err_code", payload.get("status_code")))
    if code is not None:
        try:
            normalized_code = int(code)
        except (TypeError, ValueError):
            normalized_code = None
        if normalized_code is not None and normalized_code not in SUCCESS_CODES:
            message = payload.get("message") or payload.get("msg") or payload.get("error") or response_text
            raise AsrRequestError(f"ASR code {code}: {message}")

    error = payload.get("error")
    if error:
        raise AsrRequestError(str(error))


def call_asr(audio_path: Path, endpoint: str | None = None) -> str:
    payload = build_payload(
        audio_path=audio_path,
        app_id=env("VOLC_ASR_APP_ID"),
        token=env("VOLC_ASR_ACCESS_TOKEN"),
        cluster=env("VOLC_ASR_CLUSTER"),
    )
    return post_payload(payload, endpoint=endpoint)


def call_asr_url(audio_url: str, endpoint: str | None = None) -> str:
    payload = build_url_payload(
        audio_url=audio_url,
        app_id=env("VOLC_ASR_APP_ID"),
        token=env("VOLC_ASR_ACCESS_TOKEN"),
        cluster=env("VOLC_ASR_CLUSTER"),
    )
    return post_payload(payload, endpoint=endpoint)


def extract_text(response_text: str) -> str:
    """Best-effort text extraction across common ASR response shapes."""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return response_text.strip()

    candidates = [
        payload.get("text"),
        payload.get("result", {}).get("text") if isinstance(payload.get("result"), dict) else None,
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    utterances = None
    result = payload.get("result")
    if isinstance(result, dict):
        utterances = result.get("utterances")
    if utterances is None:
        utterances = payload.get("utterances")
    if isinstance(utterances, list):
        parts = [item.get("text", "") for item in utterances if isinstance(item, dict)]
        text = "\n".join(part.strip() for part in parts if part.strip())
        if text:
            return text

    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", help="Path to WAV/M4A audio.")
    parser.add_argument("--output", help="Where to write the raw ASR JSON.")
    parser.add_argument("--text-output", help="Where to write extracted transcript text.")
    parser.add_argument("--endpoint", default=os.environ.get("VOLC_ASR_ENDPOINT", DEFAULT_ENDPOINT))
    args = parser.parse_args()

    audio_path = Path(args.audio)
    try:
        response_text = call_asr(audio_path, endpoint=args.endpoint)
    except AsrRequestError as exc:
        raise SystemExit(str(exc)) from exc
    transcript = extract_text(response_text)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(response_text, encoding="utf-8")
    if args.text_output:
        Path(args.text_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.text_output).write_text(transcript, encoding="utf-8")
    print(transcript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
