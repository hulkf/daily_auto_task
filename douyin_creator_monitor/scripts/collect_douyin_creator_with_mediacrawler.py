"""Collect and normalize a Douyin creator's works through MediaCrawler.

This script replaces the fragile browser/Crawlio capture layer with a stable
adapter around the external MediaCrawler project, while preserving this
project's downstream contract:

- output a normalized JSON payload with a top-level ``works`` list;
- keep the same field names consumed by ``sync_douyin_works_to_feishu.py``;
- require publish time and engagement metrics before the result is considered
  valid for Feishu sync.

The script does not vendor MediaCrawler. Point ``--media-crawler-dir`` or the
``MEDIACRAWLER_DIR`` environment variable at a local MediaCrawler checkout.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable


PROJECT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_DIR / "runtime"
DEFAULT_OUTPUT_FILE = PROJECT_DIR / "runtime" / "zhiliao-works-from-mediacrawler.json"
DEFAULT_MEDIA_OUTPUT_DIR = PROJECT_DIR / "runtime" / "mediacrawler-output"
BEIJING_TZ = timezone(timedelta(hours=8))
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "aweme_id": ("aweme_id", "awemeId", "note_id", "id", "作品ID", "抖音作品ID"),
    "desc": ("desc", "title", "display_title", "content", "原始文案", "作品标题"),
    "create_time": ("create_time", "publish_time", "time", "发布时间"),
    "digg_count": ("digg_count", "liked_count", "like_count", "likes", "点赞数"),
    "comment_count": ("comment_count", "comments_count", "comments", "评论数"),
    "collect_count": ("collect_count", "collected_count", "favorite_count", "收藏数"),
    "share_count": ("share_count", "shares_count", "shares", "分享数"),
    "cover_url": ("cover_url", "cover", "video_cover", "封面图URL"),
    "url": ("url", "aweme_url", "note_url", "作品链接"),
    "is_top": ("is_top", "isTop", "是否置顶"),
}


def now_beijing() -> str:
    return datetime.now(tz=timezone.utc).astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def creator_id_from_url(value: str) -> str:
    """Extract the Douyin sec_user_id/user token from a profile URL or raw id."""

    value = value.strip()
    match = re.search(r"/user/([^/?#]+)", value)
    if match:
        return match.group(1)
    return value.rstrip("/")


def resolve_media_crawler_dir(value: str | None) -> Path:
    media_dir = Path(value or os.environ.get("MEDIACRAWLER_DIR", "")).expanduser()
    if not str(media_dir) or not (media_dir / "main.py").exists():
        raise SystemExit("Missing MediaCrawler checkout. Pass --media-crawler-dir or set MEDIACRAWLER_DIR.")
    return media_dir.resolve()


def resolve_media_crawler_python(media_dir: Path, value: str | None) -> str:
    if value:
        return str(Path(value).expanduser())
    env_value = os.environ.get("MEDIACRAWLER_PYTHON")
    if env_value:
        return str(Path(env_value).expanduser())
    local_venv_python = media_dir / ".venv" / "Scripts" / "python.exe"
    if local_venv_python.exists():
        return str(local_venv_python)
    return sys.executable


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("w") or text.endswith("W"):
        multiplier = 10_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def as_unix_seconds(value: Any) -> int | None:
    parsed = as_int(value)
    if parsed is not None:
        if parsed > 10_000_000_000:
            return int(parsed / 1000)
        return parsed
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed_dt = datetime.strptime(text[:19] if "%H" in fmt else text[:10], fmt)
            return int(parsed_dt.replace(tzinfo=BEIJING_TZ).timestamp())
        except ValueError:
            continue
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def first_present(source: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


def aliased(source: dict[str, Any], field: str) -> Any:
    return first_present(source, FIELD_ALIASES[field])


def nested(source: dict[str, Any], *keys: str) -> Any:
    current: Any = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_url(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        for item in value:
            found = first_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("url_list", "url", "uri", "cover", "origin_cover"):
            found = first_url(value.get(key))
            if found:
                return found
    return None


def normalize_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize either MediaCrawler's stored shape or Douyin's raw API shape."""

    statistics = record.get("statistics") if isinstance(record.get("statistics"), dict) else {}
    aweme_id = aliased(record, "aweme_id")
    if aweme_id is None:
        return None

    desc = aliased(record, "desc") or ""
    create_time = as_unix_seconds(aliased(record, "create_time"))

    digg_count = as_int(
        aliased(record, "digg_count")
        if aliased(record, "digg_count") is not None
        else statistics.get("digg_count")
    )
    comment_count = as_int(
        aliased(record, "comment_count")
        if aliased(record, "comment_count") is not None
        else statistics.get("comment_count")
    )
    collect_count = as_int(
        aliased(record, "collect_count")
        if aliased(record, "collect_count") is not None
        else statistics.get("collect_count")
    )
    share_count = as_int(
        aliased(record, "share_count")
        if aliased(record, "share_count") is not None
        else statistics.get("share_count")
    )

    cover_url = first_url(
        aliased(record, "cover_url")
        or nested(record, "video", "cover")
        or nested(record, "video", "origin_cover")
    )
    url = aliased(record, "url")
    if not url:
        url = f"https://www.douyin.com/video/{aweme_id}"

    normalized: dict[str, Any] = {
        "aweme_id": str(aweme_id),
        "desc": str(desc),
        "create_time": create_time,
        "digg_count": digg_count,
        "comment_count": comment_count,
        "collect_count": collect_count,
        "share_count": share_count,
        "cover_url": cover_url,
        "url": url,
        "is_top": bool(aliased(record, "is_top")),
        "source": "mediacrawler",
        "raw": record,
    }
    return {key: value for key, value in normalized.items() if value is not None}


def read_json_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return []
    payload = json.loads(text)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("aweme_list", "works", "data", "items", "notes"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            records.append(item)
    return records


def read_csv_records(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def candidate_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for pattern in ("*.json", "*.jsonl", "*.csv"):
        files.extend(root.rglob(pattern))
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def load_records_from_output(root: Path) -> tuple[list[dict[str, Any]], list[Path]]:
    all_records: list[dict[str, Any]] = []
    used_files: list[Path] = []
    for path in candidate_files(root):
        name = path.name.lower()
        # MediaCrawler's official Douyin JSONL writer stores creator works as
        # ``douyin/jsonl/creator_contents_YYYY-MM-DD.jsonl``.  The file name
        # does not contain ``douyin`` or ``aweme``, so keep ``contents`` here
        # and rely on normalize_record() to reject non-Douyin rows.
        if not any(token in name for token in ("douyin", "dy", "aweme", "note", "works", "contents")):
            continue
        try:
            if path.suffix.lower() == ".jsonl":
                records = read_jsonl_records(path)
            elif path.suffix.lower() == ".csv":
                records = read_csv_records(path)
            else:
                records = read_json_records(path)
        except Exception:
            continue
        normalized = [item for item in (normalize_record(record) for record in records) if item]
        if normalized:
            all_records.extend(normalized)
            used_files.append(path)
    return all_records, used_files


def dedupe_works(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        work_id = str(record["aweme_id"])
        previous = by_id.get(work_id)
        if not previous:
            by_id[work_id] = record
            continue
        previous_score = sum(previous.get(key) is not None for key in CORE_KEYS)
        current_score = sum(record.get(key) is not None for key in CORE_KEYS)
        if current_score >= previous_score:
            by_id[work_id] = record
    return sorted(by_id.values(), key=lambda item: int(item.get("create_time") or 0), reverse=True)


CORE_KEYS = ("aweme_id", "create_time", "digg_count", "comment_count", "collect_count", "share_count")


def validate_core_fields(works: list[dict[str, Any]]) -> None:
    missing = [work.get("aweme_id") for work in works if any(work.get(key) is None or work.get(key) == "" for key in CORE_KEYS)]
    if missing:
        raise SystemExit(f"Refusing to write normalized output; missing core fields for works: {missing[:10]}")


def safe_source_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR.resolve()))
    except ValueError:
        return path.name


def build_payload(works: list[dict[str, Any]], creator_url: str, used_files: list[Path]) -> dict[str, Any]:
    return {
        "source": "MediaCrawler",
        "creator_url": creator_url,
        "creator_id": creator_id_from_url(creator_url),
        "captured_at": now_beijing(),
        "count": len(works),
        "works": works,
        "media_crawler_files": [safe_source_path(path) for path in used_files],
    }


def write_payload(payload: dict[str, Any], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_mediacrawler(args: argparse.Namespace) -> None:
    media_dir = resolve_media_crawler_dir(args.media_crawler_dir)

    output_dir = Path(args.media_output_dir)
    if args.clean_media_output and output_dir.exists():
        resolved_output = output_dir.resolve()
        resolved_runtime = RUNTIME_DIR.resolve()
        if resolved_output == resolved_runtime or resolved_runtime not in resolved_output.parents:
            raise SystemExit(f"Refusing to clean output directory outside project runtime/: {resolved_output}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bootstrap = PROJECT_DIR / "runtime" / "run_mediacrawler_douyin_creator.py"
    creator_id = creator_id_from_url(args.creator_url)
    bootstrap.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import runpy, sys",
                f"media_dir = Path({str(media_dir)!r})",
                "sys.path.insert(0, str(media_dir))",
                "import config",
                f"config.DY_CREATOR_ID_LIST = [{creator_id!r}]",
                f"config.CRAWLER_MAX_NOTES_COUNT = {int(args.max_count)}",
                f"config.SAVE_DATA_OPTION = {args.save_data_option!r}",
                f"config.SAVE_DATA_PATH = {str(output_dir)!r}",
                "sys.argv = [",
                "    str(media_dir / 'main.py'),",
                "    '--platform', 'dy',",
                "    '--type', 'creator',",
                "    '--lt', " + repr(args.login_type) + ",",
                "    '--creator_id', " + repr(creator_id) + ",",
                "    '--crawler_max_notes_count', " + repr(str(int(args.max_count))) + ",",
                "    '--save_data_option', " + repr(args.save_data_option) + ",",
                "    '--save_data_path', " + repr(str(output_dir)) + ",",
                "    '--get_comment', 'false',",
                "]",
                "runpy.run_path(str(media_dir / 'main.py'), run_name='__main__')",
            ]
        ),
        encoding="utf-8",
    )
    command = [resolve_media_crawler_python(media_dir, args.media_crawler_python), str(bootstrap)]
    result = subprocess.run(command, cwd=media_dir, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def collect(args: argparse.Namespace) -> dict[str, Any]:
    if not args.normalize_only:
        run_mediacrawler(args)

    records, used_files = load_records_from_output(Path(args.media_output_dir))
    if not records:
        try:
            media_dir = resolve_media_crawler_dir(args.media_crawler_dir)
        except SystemExit:
            media_dir = None
        if media_dir:
            records, used_files = load_records_from_output(media_dir / "data")
    works = dedupe_works(records)
    if args.expect_min_count and len(works) < args.expect_min_count:
        raise SystemExit(f"Only found {len(works)} works, below --expect-min-count={args.expect_min_count}.")
    if not works:
        raise SystemExit("No Douyin works found in MediaCrawler output.")
    validate_core_fields(works)
    payload = build_payload(works, args.creator_url, used_files)
    write_payload(payload, Path(args.output_file))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Douyin creator works through MediaCrawler and normalize them.")
    parser.add_argument("--creator-url", required=True, help="Douyin creator profile URL or sec_user_id.")
    parser.add_argument("--media-crawler-dir", help="Local MediaCrawler project checkout. Can also use MEDIACRAWLER_DIR.")
    parser.add_argument("--media-crawler-python", help="Python executable with MediaCrawler dependencies. Defaults to MEDIACRAWLER_PYTHON or MediaCrawler/.venv.")
    parser.add_argument("--media-output-dir", default=str(DEFAULT_MEDIA_OUTPUT_DIR))
    parser.add_argument("--output-file", default=str(DEFAULT_OUTPUT_FILE))
    parser.add_argument("--max-count", type=int, default=200)
    parser.add_argument("--expect-min-count", type=int, default=1)
    parser.add_argument("--login-type", default="qrcode", choices=["qrcode", "phone", "cookie"])
    parser.add_argument("--save-data-option", default="jsonl", choices=["jsonl", "json", "csv"])
    parser.add_argument("--normalize-only", action="store_true", help="Skip running MediaCrawler; only normalize existing output files.")
    parser.add_argument("--clean-media-output", action="store_true")
    args = parser.parse_args()
    payload = collect(args)
    print(json.dumps({"output_file": str(Path(args.output_file)), "count": payload["count"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
