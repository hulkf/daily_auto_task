"""Back up transcript TXT files to Quark Cloud Drive via kuake CLI.

Credentials are read from environment variables or a local ignored JSON file:

- KUAKE_COOKIE, or KUAKE_PUS + KUAKE_PUUS
- douyin_creator_monitor/local/kuake.env.json

The CLI binary is expected at tools/kuake-cli/kuake.exe by default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
DEFAULT_LOCAL_ENV_PATH = PROJECT_ROOT / "local" / "kuake.env.json"
DEFAULT_KUAKE_EXE = REPO_ROOT / "tools" / "kuake-cli" / "kuake.exe"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


class KuakeBackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class KuakeCredentials:
    cookie: str = ""
    pus: str = ""
    puus: str = ""


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise KuakeBackupError(f"配置文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise KuakeBackupError(f"配置文件不是合法 JSON: {path}") from exc


def normalize_cookie(value: str) -> str:
    """Normalize cookies copied from chat/Markdown without printing them."""
    return value.strip().replace("\\_", "_").replace("*", "")


def sanitize_path_part(value: str, max_length: int = 80) -> str:
    """Make one file/folder path segment safe for cloud-drive and Windows usage."""
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    if not text:
        return "未命名"
    return text[:max_length].rstrip(" ._") or "未命名"


def build_transcript_filename(video_date: str, video_id: str, title: str) -> str:
    """Build transcript filename as 日期_视频ID_标题.txt."""
    safe_date = sanitize_path_part(video_date, max_length=20)
    safe_video_id = sanitize_path_part(video_id, max_length=80)
    safe_title = sanitize_path_part(title, max_length=100)
    return f"{safe_date}_{safe_video_id}_{safe_title}.txt"


def join_remote_path(*parts: str) -> str:
    clean_parts = [str(part).strip().strip("/") for part in parts if str(part or "").strip().strip("/")]
    return "/" + "/".join(clean_parts) if clean_parts else "/"


def load_credentials(local_env_path: Path = DEFAULT_LOCAL_ENV_PATH) -> tuple[KuakeCredentials, str]:
    cookie = os.environ.get("KUAKE_COOKIE", "").strip()
    pus = os.environ.get("KUAKE_PUS", "").strip()
    puus = os.environ.get("KUAKE_PUUS", "").strip()
    default_backup_dir = "/视频文案备份"

    if (not cookie and (not pus or not puus)) and local_env_path.exists():
        data = load_json(local_env_path)
        cookie = str(data.get("KUAKE_COOKIE") or data.get("kuake_cookie") or "").strip()
        pus = str(data.get("KUAKE_PUS") or data.get("kuake_pus") or "").strip()
        puus = str(data.get("KUAKE_PUUS") or data.get("kuake_puus") or "").strip()
        default_backup_dir = str(data.get("default_backup_dir") or default_backup_dir).strip() or default_backup_dir

    if cookie:
        return KuakeCredentials(cookie=normalize_cookie(cookie)), default_backup_dir
    if pus and puus:
        return KuakeCredentials(pus=normalize_cookie(pus), puus=normalize_cookie(puus)), default_backup_dir
    raise KuakeBackupError(
        "缺少夸克网盘凭证。请设置 KUAKE_COOKIE，或 KUAKE_PUS/KUAKE_PUUS，"
        "或在 douyin_creator_monitor/local/kuake.env.json 中配置。"
    )


def kuake_env(credentials: KuakeCredentials) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("KUAKE_COOKIE", None)
    env.pop("KUAKE_PUS", None)
    env.pop("KUAKE_PUUS", None)
    if credentials.cookie:
        env["KUAKE_COOKIE"] = credentials.cookie
    else:
        env["KUAKE_PUS"] = credentials.pus
        env["KUAKE_PUUS"] = credentials.puus
    return env


def run_kuake(kuake_exe: Path, credentials: KuakeCredentials, args: list[str]) -> dict[str, Any]:
    if not kuake_exe.exists():
        raise KuakeBackupError(f"找不到 kuake CLI: {kuake_exe}")
    completed = subprocess.run(
        [str(kuake_exe), *args],
        cwd=str(REPO_ROOT),
        env=kuake_env(credentials),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    output = completed.stdout.strip() or completed.stderr.strip()
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise KuakeBackupError(f"kuake 返回了非 JSON 响应: {output[:500]}") from exc
    if completed.returncode != 0 or not result.get("success"):
        raise KuakeBackupError(str(result.get("message") or result))
    return result


def list_dir(kuake_exe: Path, credentials: KuakeCredentials, path: str) -> list[dict[str, Any]]:
    result = run_kuake(kuake_exe, credentials, ["list", path])
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    items = data.get("list") if isinstance(data.get("list"), list) else []
    return [item for item in items if isinstance(item, dict)]


def split_remote_path(path: str) -> tuple[str, str]:
    clean = path.strip().replace("\\", "/")
    if not clean.startswith("/"):
        clean = "/" + clean
    clean = clean.rstrip("/")
    if clean in {"", "/"}:
        return "/", ""
    parent, _, name = clean.rpartition("/")
    return parent or "/", name


def ensure_dir(kuake_exe: Path, credentials: KuakeCredentials, path: str) -> str:
    clean = path.strip().replace("\\", "/") or "/"
    if not clean.startswith("/"):
        clean = "/" + clean
    if clean == "/":
        return "/"
    current = "/"
    for part in [part for part in clean.split("/") if part]:
        items = list_dir(kuake_exe, credentials, current)
        existing = next((item for item in items if item.get("dir") and item.get("file_name") == part), None)
        if existing:
            current = str(existing.get("path") or f"{current.rstrip('/')}/{part}")
            continue
        run_kuake(kuake_exe, credentials, ["create", part, current])
        current = f"{current.rstrip('/')}/{part}"
    return current


def upload_file(
    kuake_exe: Path,
    credentials: KuakeCredentials,
    local_file: Path,
    remote_dir: str,
    remote_name: str | None = None,
    create_dir: bool = False,
) -> str:
    if not local_file.exists() or not local_file.is_file():
        raise KuakeBackupError(f"本地文件不存在: {local_file}")
    if local_file.suffix.lower() != ".txt":
        raise KuakeBackupError(f"当前脚本只上传 TXT 文案文件: {local_file}")
    target_dir = ensure_dir(kuake_exe, credentials, remote_dir) if create_dir else remote_dir
    name = remote_name or local_file.name
    remote_path = f"{target_dir.rstrip('/')}/{name}" if target_dir != "/" else f"/{name}"
    run_kuake(kuake_exe, credentials, ["upload", str(local_file.resolve()), remote_path])
    return remote_path


def cmd_list(args: argparse.Namespace) -> int:
    credentials, _ = load_credentials(args.local_env)
    items = list_dir(args.kuake_exe, credentials, args.path)
    for item in items:
        kind = "目录" if item.get("dir") else "文件"
        print(f"{kind}\t{item.get('path', '')}\t{item.get('fid', '')}")
    return 0


def cmd_ensure_dir(args: argparse.Namespace) -> int:
    credentials, _ = load_credentials(args.local_env)
    path = ensure_dir(args.kuake_exe, credentials, args.path)
    print(f"已确认目录: {path}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    credentials, default_backup_dir = load_credentials(args.local_env)
    base_dir = args.base_dir or default_backup_dir
    if args.creator_name:
        remote_dir = args.remote_dir or join_remote_path(base_dir, sanitize_path_part(args.creator_name))
    else:
        remote_dir = args.remote_dir or base_dir
    remote_name = args.remote_name
    if not remote_name and (args.video_date or args.video_id or args.title):
        missing = [
            name
            for name, value in [("--video-date", args.video_date), ("--video-id", args.video_id), ("--title", args.title)]
            if not value
        ]
        if missing:
            raise KuakeBackupError(f"自动命名需要同时提供: {', '.join(missing)}")
        remote_name = build_transcript_filename(args.video_date, args.video_id, args.title)
    remote_path = upload_file(
        args.kuake_exe,
        credentials,
        args.file,
        remote_dir=remote_dir,
        remote_name=remote_name,
        create_dir=args.create_dir,
    )
    print(f"已上传: {args.file} -> {remote_path}")
    return 0


def cmd_upload_dir(args: argparse.Namespace) -> int:
    credentials, default_backup_dir = load_credentials(args.local_env)
    base_dir = args.base_dir or default_backup_dir
    if args.creator_name:
        remote_dir = args.remote_dir or join_remote_path(base_dir, sanitize_path_part(args.creator_name))
    else:
        remote_dir = args.remote_dir or base_dir
    files = sorted(args.input_dir.glob(args.pattern))
    txt_files = [path for path in files if path.is_file() and path.suffix.lower() == ".txt"]
    if not txt_files:
        raise KuakeBackupError(f"目录中没有匹配的 TXT 文件: {args.input_dir} / {args.pattern}")
    for path in txt_files:
        remote_path = upload_file(
            args.kuake_exe,
            credentials,
            path,
            remote_dir=remote_dir,
            create_dir=args.create_dir,
        )
        print(f"已上传: {path} -> {remote_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="备份火山 ASR TXT 文案到夸克网盘")
    parser.add_argument("--local-env", type=Path, default=DEFAULT_LOCAL_ENV_PATH, help="本地夸克凭证 JSON")
    parser.add_argument("--kuake-exe", type=Path, default=DEFAULT_KUAKE_EXE, help="kuake CLI 路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出夸克网盘目录")
    list_parser.add_argument("path", nargs="?", default="/")
    list_parser.set_defaults(func=cmd_list)

    ensure_parser = subparsers.add_parser("ensure-dir", help="确认目录存在，不存在则创建")
    ensure_parser.add_argument("path")
    ensure_parser.set_defaults(func=cmd_ensure_dir)

    upload_parser = subparsers.add_parser("upload", help="上传单个 TXT 文案")
    upload_parser.add_argument("--file", type=Path, required=True)
    upload_parser.add_argument("--base-dir", default="", help="夸克备份根目录，默认读取 local 配置")
    upload_parser.add_argument("--creator-name", default="", help="博主名称；提供后默认上传到 根目录/博主名称")
    upload_parser.add_argument("--video-date", default="", help="视频发布日期，如 2026-07-18")
    upload_parser.add_argument("--video-id", default="", help="视频作品 ID")
    upload_parser.add_argument("--title", default="", help="视频标题/文案标题，用于生成文件名")
    upload_parser.add_argument("--remote-dir", default="", help="夸克目标目录，默认读取 local 配置")
    upload_parser.add_argument("--remote-name", default="", help="上传后的文件名，默认用本地文件名")
    upload_parser.add_argument("--create-dir", action="store_true", help="目标目录不存在时自动创建")
    upload_parser.set_defaults(func=cmd_upload)

    upload_dir_parser = subparsers.add_parser("upload-dir", help="上传目录中的 TXT 文案")
    upload_dir_parser.add_argument("--input-dir", type=Path, required=True)
    upload_dir_parser.add_argument("--pattern", default="*.txt")
    upload_dir_parser.add_argument("--base-dir", default="", help="夸克备份根目录，默认读取 local 配置")
    upload_dir_parser.add_argument("--creator-name", default="", help="博主名称；提供后默认上传到 根目录/博主名称")
    upload_dir_parser.add_argument("--remote-dir", default="", help="夸克目标目录，默认读取 local 配置")
    upload_dir_parser.add_argument("--create-dir", action="store_true", help="目标目录不存在时自动创建")
    upload_dir_parser.set_defaults(func=cmd_upload_dir)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KuakeBackupError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
