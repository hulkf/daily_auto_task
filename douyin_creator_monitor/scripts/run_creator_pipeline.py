#!/usr/bin/env python3
"""Run the end-to-end Douyin creator transcript pipeline.

Stages: collect -> Feishu sync -> ASR -> correction -> Feishu writeback
-> IMA / Quark / Obsidian backup.

Existing CLI modules remain the source of truth. This file only coordinates
those modules and records resumable runtime state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_DIR / "scripts"
DEFAULT_CONFIG = PROJECT_DIR / "local" / "pipeline.json"
DEFAULT_STATE_DIR = PROJECT_DIR / "runtime" / "pipeline"
DEFAULT_MEDIA_DIR = PROJECT_DIR / "runtime" / "media"
DEFAULT_LOG_DIR = PROJECT_DIR / "logs"
BEIJING_TZ = timezone(timedelta(hours=8))
HASHTAG_RE = re.compile(r"#([^#\s]+)")
INVALID_PATH_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
STAGES = (
    "collected", "feishu_synced", "transcribed", "corrected",
    "feishu_written_back", "ima_backed_up", "kuake_backed_up",
    "obsidian_exported",
)


class PipelineError(RuntimeError):
    pass


class Logger:
    def __init__(self, path: Path, *, persist: bool = True) -> None:
        self.path = path
        self.persist = persist
        self._lock = threading.Lock()
        if persist:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, message: str) -> None:
        line = f"[{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        with self._lock:
            if self.persist:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            print(line)


class Runner:
    def __init__(self, logger: Logger, dry_run: bool) -> None:
        self.logger = logger
        self.dry_run = dry_run

    @staticmethod
    def display(command: list[str], sensitive: Iterable[str]) -> str:
        hidden = set(sensitive)
        result: list[str] = []
        mask_next = False
        for part in command:
            if mask_next:
                result.append("<redacted>")
                mask_next = False
            else:
                result.append(part)
                mask_next = part in hidden
        return subprocess.list2cmdline(result)

    def run(
        self,
        label: str,
        command: list[str],
        env: dict[str, str],
        *,
        sensitive: Iterable[str] = (),
    ) -> str:
        shown = self.display(command, sensitive)
        if self.dry_run:
            self.logger.write(f"[DRY-RUN] {label}: {shown}")
            return ""
        self.logger.write(f"开始 {label}: {shown}")
        result = subprocess.run(
            command,
            cwd=PROJECT_DIR,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stdout:
            self.logger.write(f"{label} stdout: {truncate(stdout)}")
        if stderr:
            self.logger.write(f"{label} stderr: {truncate(stderr)}")
        if result.returncode:
            raise PipelineError(f"{label} 失败，退出码 {result.returncode}: {stderr or stdout}")
        self.logger.write(f"完成 {label}")
        return stdout


def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f" ... <省略 {len(text) - limit} 个字符>"


def now_text() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PipelineError(f"文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"JSON 格式错误: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"JSON 顶层必须是对象: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def path_from(value: Any, default: Path | None = None) -> Path | None:
    if value in (None, ""):
        return default
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    return value if isinstance(value, dict) else {}


def chosen(creator: dict[str, Any], defaults: dict[str, Any], key: str, fallback: Any = None) -> Any:
    return creator.get(key) if creator.get(key) not in (None, "") else defaults.get(key, fallback)


def safe_key(value: str) -> str:
    return INVALID_PATH_RE.sub("_", value).strip(" ._") or "creator"


def creator_key(creator: dict[str, Any]) -> str:
    value = str(creator.get("key") or creator.get("creator_dir_name") or creator.get("creator_name") or "").strip()
    if not value:
        raise PipelineError("达人配置缺少 key、creator_dir_name 和 creator_name。")
    return safe_key(value)


def append_option(command: list[str], option: str, value: Any) -> None:
    if value not in (None, ""):
        command.extend([option, str(value)])


def py(config: dict[str, Any], script: str, *args: Any) -> list[str]:
    return [str(config.get("python") or sys.executable), str(SCRIPTS_DIR / script), *map(str, args)]


def nonempty(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def raw_work(work: dict[str, Any]) -> dict[str, Any]:
    value = work.get("raw")
    return value if isinstance(value, dict) else {}


def audio_url(work: dict[str, Any]) -> str:
    for value in (work.get("music_download_url"), raw_work(work).get("music_download_url")):
        if isinstance(value, list):
            value = next((item for item in value if isinstance(item, str) and item.strip()), "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def title_of(work: dict[str, Any]) -> str:
    raw = raw_work(work)
    value = str(raw.get("title") or work.get("title") or work.get("desc") or work.get("aweme_id") or "")
    value = HASHTAG_RE.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:120] or str(work.get("aweme_id") or "未命名作品")


def date_of(work: dict[str, Any]) -> str:
    try:
        return datetime.fromtimestamp(float(work.get("create_time")), timezone.utc).astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")


def artifact_paths(media_dir: Path, work_id: str, provider: str) -> dict[str, Path]:
    provider_name = "volc-url" if provider == "volcengine" else "bailian-url"
    return {
        "raw_json": media_dir / f"{work_id}.{provider_name}.json",
        "raw_text": media_dir / f"{work_id}.{provider_name}.txt",
        "final": media_dir / f"{work_id}.final.txt",
        "report": media_dir / f"{work_id}.correction-report.json",
    }


def load_works(path: Path) -> list[dict[str, Any]]:
    works = read_json(path).get("works")
    if not isinstance(works, list):
        raise PipelineError(f"作品文件缺少 works 数组: {path}")
    return [item for item in works if isinstance(item, dict) and item.get("aweme_id")]


def select_works(works: list[dict[str, Any]], ids: set[str], maximum: int) -> list[dict[str, Any]]:
    selected = [w for w in works if not ids or str(w.get("aweme_id")) in ids]
    if ids:
        missing = ids - {str(w.get("aweme_id")) for w in selected}
        if missing:
            raise PipelineError(f"作品文件中找不到指定作品 ID: {sorted(missing)}")
    selected.sort(key=lambda w: int(w.get("create_time") or 0), reverse=True)
    return selected[:maximum] if maximum > 0 else selected


def load_state(path: Path, creator: dict[str, Any], work: dict[str, Any]) -> dict[str, Any]:
    try:
        state = read_json(path) if path.exists() else {}
    except PipelineError:
        state = {}
    state.setdefault("creator_key", creator_key(creator))
    state.setdefault("creator_name", str(creator.get("creator_name") or creator.get("creator_dir_name") or ""))
    state.setdefault("aweme_id", str(work.get("aweme_id")))
    state.setdefault("title", title_of(work))
    state.setdefault("stages", {})
    return state


def status_of(state: dict[str, Any], stage: str) -> str:
    value = state.get("stages", {}).get(stage, {})
    return str(value.get("status") or "") if isinstance(value, dict) else ""


def set_status(state: dict[str, Any], stage: str, status: str, detail: str = "", inferred: bool = False) -> None:
    item: dict[str, Any] = {"status": status, "updated_at": now_text()}
    if detail:
        item["detail"] = detail
    if inferred:
        item["inferred"] = True
    state.setdefault("stages", {})[stage] = item
    state["updated_at"] = now_text()


def should_skip(
    state: dict[str, Any],
    stage: str,
    resume: bool,
    force: set[str],
    required: Iterable[Path] = (),
) -> bool:
    if not resume or "all" in force or stage in force or status_of(state, stage) != "success":
        return False
    return all(nonempty(path) for path in required)


def execute_stage(
    stage: str,
    label: str,
    state: dict[str, Any],
    state_path: Path,
    args: argparse.Namespace,
    logger: Logger,
    action: Callable[[], None],
    required: Iterable[Path] = (),
) -> str:
    files = tuple(required)
    if should_skip(state, stage, args.resume, args.force_stage, files):
        logger.write(f"跳过 {label}：状态已完成")
        return "skipped"
    try:
        action()
        if not args.dry_run:
            missing = [str(path) for path in files if not nonempty(path)]
            if missing:
                raise PipelineError(f"{label} 未生成预期文件: {missing}")
            set_status(state, stage, "success")
            write_json(state_path, state)
        return "planned" if args.dry_run else "success"
    except Exception as exc:  # Keep other backups and works running.
        logger.write(f"失败 {label}: {exc}")
        if not args.dry_run:
            set_status(state, stage, "failed", str(exc))
            write_json(state_path, state)
        return "failed"


def child_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    feishu = section(config, "feishu")
    source_name = str(feishu.get("base_token_env") or "FEISHU_BASE_TOKEN")
    token = env.get(source_name, "").strip()
    if not token:
        ids_file = path_from(feishu.get("ids_file"), PROJECT_DIR / "local" / "feishu-ids.md")
        if ids_file and ids_file.exists():
            match = re.search(r"base_token:\s*([A-Za-z0-9]+)", ids_file.read_text(encoding="utf-8-sig"))
            token = match.group(1) if match else ""
    if token:
        env["FEISHU_BASE_TOKEN"] = token
    return env


def collect_command(
    config: dict[str, Any], creator: dict[str, Any], works_file: Path,
    media_output: Path, normalize_only: bool,
) -> list[str]:
    defaults = section(config, "collection")
    creator_url = str(creator.get("creator_url") or "").strip()
    if not creator_url:
        raise PipelineError(f"达人 {creator_key(creator)} 缺少 creator_url。")
    command = py(
        config, "collect_douyin_creator_with_mediacrawler.py",
        "--creator-url", creator_url,
        "--media-output-dir", media_output,
        "--output-file", works_file,
        "--max-count", chosen(creator, defaults, "max_count", 200),
        "--expect-min-count", chosen(creator, defaults, "expect_min_count", 1),
        "--login-type", chosen(creator, defaults, "login_type", "qrcode"),
        "--save-data-option", chosen(creator, defaults, "save_data_option", "jsonl"),
    )
    append_option(command, "--media-crawler-dir", chosen(creator, defaults, "media_crawler_dir"))
    append_option(command, "--media-crawler-python", chosen(creator, defaults, "media_crawler_python"))
    if chosen(creator, defaults, "clean_media_output", False):
        command.append("--clean-media-output")
    if normalize_only:
        command.append("--normalize-only")
    return command


def sync_command(config: dict[str, Any], creator: dict[str, Any], works_file: Path) -> list[str]:
    table = str(creator.get("works_table_id") or "").strip()
    if not table:
        raise PipelineError(f"达人 {creator_key(creator)} 缺少 works_table_id。")
    command = py(config, "sync_douyin_works_to_feishu.py", "--works-file", works_file, "--table-id", table)
    append_option(command, "--lark-cli", section(config, "feishu").get("lark_cli"))
    return command


def transcribe_command(
    config: dict[str, Any], creator: dict[str, Any], url: str, paths: dict[str, Path],
) -> list[str]:
    defaults = section(config, "asr")
    command = py(
        config, "transcribe_douyin_audio_url.py",
        "--audio-url", url,
        "--provider", chosen(creator, defaults, "provider", "volcengine"),
        "--mode", chosen(creator, defaults, "mode", "direct-url"),
        "--json-output", paths["raw_json"],
        "--text-output", paths["raw_text"],
    )
    for option, key in (
        ("--ffmpeg", "ffmpeg"), ("--fallback-upload-command", "fallback_upload_command"),
        ("--model", "model"), ("--endpoint", "endpoint"),
    ):
        append_option(command, option, chosen(creator, defaults, key))
    return command


def correction_command(config: dict[str, Any], creator: dict[str, Any], paths: dict[str, Path]) -> list[str]:
    defaults = section(config, "correction")
    command = py(
        config, "correct_transcript.py", paths["raw_text"],
        "--output", paths["final"], "--report-output", paths["report"],
    )
    glossary = chosen(creator, defaults, "glossary")
    append_option(command, "--glossary", path_from(glossary) if glossary else None)
    domains = creator.get("correction_domains") or creator.get("correction_domain") or defaults.get("domains") or defaults.get("domain")
    if isinstance(domains, str):
        domains = [domains]
    if isinstance(domains, list):
        for domain in domains:
            append_option(command, "--domain", domain)
    return command


def writeback_command(config: dict[str, Any], creator: dict[str, Any], work: dict[str, Any], paths: dict[str, Path]) -> list[str]:
    table = str(creator.get("works_table_id") or "").strip()
    if not table:
        raise PipelineError(f"达人 {creator_key(creator)} 缺少 works_table_id。")
    feishu = section(config, "feishu")
    command = py(
        config, "feishu_transcript_writer.py", "--table-id", table,
        "--work-id", work["aweme_id"], "--corrected-transcript-file", paths["final"],
    )
    append_option(command, "--work-id-field", feishu.get("work_id_field"))
    append_option(command, "--transcript-field", feishu.get("transcript_field"))
    append_option(command, "--lark-cli", feishu.get("lark_cli"))
    append_option(command, "--as", feishu.get("as_identity"))
    return command


def ima_command(config: dict[str, Any], creator: dict[str, Any], paths: dict[str, Path]) -> list[str]:
    ima = section(config, "ima")
    name = str(creator.get("ima_creator_name") or creator.get("creator_name") or creator.get("creator_dir_name") or "").strip()
    if not name:
        raise PipelineError("缺少 IMA 达人名称。")
    command = py(config, "backup_transcripts_to_ima.py")
    append_option(command, "--mapping", path_from(ima.get("mapping"), PROJECT_DIR / "local" / "ima_creator_mapping.json"))
    command.extend(["upload", "--creator-name", name, "--file", str(paths["final"]), "--on-duplicate", str(ima.get("on_duplicate") or "skip")])
    return command


def ima_ensure_command(config: dict[str, Any], creator: dict[str, Any], metadata: Path) -> list[str]:
    ima = section(config, "ima")
    name = str(creator.get("ima_creator_name") or creator.get("creator_name") or creator.get("creator_dir_name") or "").strip()
    if not name:
        raise PipelineError("缺少 IMA 达人名称。")
    command = py(config, "backup_transcripts_to_ima.py")
    append_option(command, "--mapping", path_from(ima.get("mapping"), PROJECT_DIR / "local" / "ima_creator_mapping.json"))
    command.extend(["ensure-folder", "--creator-name", name, "--metadata-output", str(metadata)])
    return command


def kuake_command(config: dict[str, Any], creator: dict[str, Any], work: dict[str, Any], paths: dict[str, Path]) -> list[str]:
    kuake = section(config, "kuake")
    name = str(creator.get("creator_dir_name") or creator.get("creator_name") or "").strip()
    if not name:
        raise PipelineError("缺少夸克达人目录名称。")
    command = py(config, "backup_transcripts_to_kuake.py")
    append_option(command, "--local-env", path_from(kuake.get("local_env"), PROJECT_DIR / "local" / "kuake.env.json"))
    if kuake.get("kuake_exe"):
        append_option(command, "--kuake-exe", path_from(kuake["kuake_exe"]))
    command.extend([
        "upload", "--file", str(paths["final"]), "--creator-name", name,
        "--video-date", date_of(work), "--video-id", str(work["aweme_id"]),
        "--title", title_of(work), "--create-dir",
    ])
    append_option(command, "--base-dir", kuake.get("base_dir"))
    append_option(command, "--remote-dir", creator.get("kuake_remote_dir"))
    return command


def kuake_ensure_command(config: dict[str, Any], creator: dict[str, Any], metadata: Path) -> list[str]:
    kuake = section(config, "kuake")
    name = str(creator.get("creator_dir_name") or creator.get("creator_name") or "").strip()
    if not name:
        raise PipelineError("缺少夸克达人目录名称。")
    command = py(config, "backup_transcripts_to_kuake.py")
    append_option(command, "--local-env", path_from(kuake.get("local_env"), PROJECT_DIR / "local" / "kuake.env.json"))
    if kuake.get("kuake_exe"):
        append_option(command, "--kuake-exe", path_from(kuake["kuake_exe"]))
    command.extend(["ensure-creator-dir", "--creator-name", name, "--metadata-output", str(metadata)])
    append_option(command, "--base-dir", kuake.get("base_dir"))
    append_option(command, "--remote-dir", creator.get("kuake_remote_dir"))
    return command


def obsidian_command(
    config: dict[str, Any], creator: dict[str, Any], work: dict[str, Any],
    works_file: Path, profile_file: Path | None, paths: dict[str, Path], overwrite: bool,
) -> list[str]:
    obsidian = section(config, "obsidian")
    command = py(
        config, "export_transcript_to_obsidian.py", "--transcript", paths["final"],
        "--aweme-id", work["aweme_id"], "--works-file", works_file,
    )
    append_option(command, "--profile-file", profile_file)
    append_option(command, "--creator-name", creator.get("creator_name"))
    append_option(command, "--creator-dir-name", creator.get("creator_dir_name"))
    append_option(command, "--obsidian-original-dir", path_from(obsidian.get("original_dir")))
    append_option(command, "--template-file", path_from(obsidian.get("template_file")))
    if overwrite:
        command.append("--overwrite")
    return command


def obsidian_ensure_command(
    config: dict[str, Any], creator: dict[str, Any], profile_file: Path | None, metadata: Path,
) -> list[str]:
    obsidian = section(config, "obsidian")
    command = py(config, "export_transcript_to_obsidian.py", "--ensure-creator-dir-only", "--metadata-output", metadata)
    append_option(command, "--profile-file", profile_file)
    append_option(command, "--creator-name", creator.get("creator_name"))
    append_option(command, "--creator-dir-name", creator.get("creator_dir_name"))
    append_option(command, "--obsidian-original-dir", path_from(obsidian.get("original_dir")))
    return command


def creator_match(creator: dict[str, Any]) -> tuple[str, str]:
    explicit_field = str(creator.get("feishu_match_field") or "").strip()
    explicit_value = str(creator.get("feishu_match_value") or "").strip()
    if explicit_field and explicit_value:
        return explicit_field, explicit_value
    creator_url = str(creator.get("creator_url") or "").strip().rstrip("/")
    if creator_url:
        sec_uid = creator_url.rsplit("/", 1)[-1].split("?", 1)[0].strip()
        if sec_uid:
            return "SecUID", sec_uid
    name = str(creator.get("creator_name") or "").strip()
    if name:
        return "达人昵称", name
    raise PipelineError("无法确定飞书达人基础表的匹配字段和值。")


def mapping_sync_command(config: dict[str, Any], creator: dict[str, Any], metadata: Path) -> list[str]:
    feishu = section(config, "feishu")
    table_id = str(feishu.get("creator_table_id") or "").strip()
    if not table_id or table_id == "REPLACE_WITH_FEISHU_CREATOR_TABLE_ID":
        raise PipelineError("feishu.creator_table_id 未配置达人基础信息表 ID。")
    match_field, match_value = creator_match(creator)
    command = py(
        config, "sync_creator_backup_mapping_to_feishu.py",
        "--table-id", table_id, "--metadata-file", metadata,
        "--match-field", match_field, "--match-value", match_value,
    )
    append_option(command, "--lark-cli", feishu.get("lark_cli"))
    append_option(command, "--as", feishu.get("as_identity"))
    return command


def ensure_and_sync_mapping(
    runner: Runner, label: str, ensure_command: list[str], sync_command: list[str], env: dict[str, str],
) -> str:
    runner.run(f"确认{label}达人目录", ensure_command, env)
    output = runner.run(
        f"回写{label}目录映射到飞书", sync_command, env,
        sensitive=("--table-id", "--base-token", "--match-value"),
    )
    if runner.dry_run:
        return "planned"
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return "success"
    status = str(payload.get("status") or "success")
    return status if status in {"skipped", "updated", "success"} else "success"


def sync_creator_backup_mappings(
    config: dict[str, Any], creator: dict[str, Any], profile_file: Path | None,
    state_dir: Path, runner: Runner, logger: Logger, env: dict[str, str], args: argparse.Namespace,
) -> dict[str, str]:
    mapping_dir = state_dir / "mappings" / creator_key(creator)
    tasks: list[tuple[str, bool, list[str], Path]] = [
        (
            "IMA", args.skip_ima or not bool(section(config, "ima").get("enabled", True)),
            ima_ensure_command(config, creator, mapping_dir / "ima.json"), mapping_dir / "ima.json",
        ),
        (
            "夸克", args.skip_kuake or not bool(section(config, "kuake").get("enabled", True)),
            kuake_ensure_command(config, creator, mapping_dir / "kuake.json"), mapping_dir / "kuake.json",
        ),
        (
            "Obsidian", args.skip_obsidian or not bool(section(config, "obsidian").get("enabled", True)),
            obsidian_ensure_command(config, creator, profile_file, mapping_dir / "obsidian.json"),
            mapping_dir / "obsidian.json",
        ),
    ]
    outcomes: dict[str, str] = {}
    for label, disabled, ensure_command, metadata in tasks:
        key = label.casefold()
        if disabled:
            outcomes[key] = "disabled"
            continue
        try:
            outcomes[key] = ensure_and_sync_mapping(
                runner, label, ensure_command,
                mapping_sync_command(config, creator, metadata), env,
            )
        except Exception as exc:
            outcomes[key] = "failed"
            logger.write(f"{label} 达人目录映射同步失败: {exc}")
            if args.fail_fast:
                raise
    return outcomes


def prepare_work(
    config: dict[str, Any], creator: dict[str, Any], work: dict[str, Any],
    works_file: Path, profile_file: Path | None, state_dir: Path, media_dir: Path,
    runner: Runner, logger: Logger, env: dict[str, str], args: argparse.Namespace,
) -> dict[str, Any]:
    work_id = str(work["aweme_id"])
    state_path = state_dir / creator_key(creator) / f"{safe_key(work_id)}.json"
    state = load_state(state_path, creator, work)
    provider = str(chosen(creator, section(config, "asr"), "provider", "volcengine"))
    paths = artifact_paths(media_dir, work_id, provider)
    media_dir.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        changed = False
        if status_of(state, "transcribed") != "success" and nonempty(paths["raw_text"]):
            set_status(state, "transcribed", "success", "检测到已有原始转写文件", True)
            changed = True
        if status_of(state, "corrected") != "success" and nonempty(paths["final"]):
            set_status(state, "corrected", "success", "检测到已有纠正后文案", True)
            changed = True
        if changed:
            write_json(state_path, state)

    logger.write(f"处理作品 {work_id}: {title_of(work)}")
    result: dict[str, Any] = {"aweme_id": work_id, "title": title_of(work), "stages": {}}
    source_url = audio_url(work)

    if args.skip_transcribe:
        result["stages"]["transcribed"] = "disabled"
    elif not source_url and not nonempty(paths["raw_text"]):
        message = "作品数据中没有 music_download_url"
        logger.write(f"转写失败 {work_id}: {message}")
        if not args.dry_run:
            set_status(state, "transcribed", "failed", message)
            write_json(state_path, state)
        result["stages"]["transcribed"] = "failed"
    else:
        result["stages"]["transcribed"] = execute_stage(
            "transcribed", f"音频转写 {work_id}", state, state_path, args, logger,
            lambda: runner.run(
                f"音频转写 {work_id}", transcribe_command(config, creator, source_url, paths), env,
                sensitive=("--audio-url", "--base-token"),
            ),
            (paths["raw_text"], paths["raw_json"]),
        )

    transcribe_outcome = result["stages"]["transcribed"]
    if args.skip_correction:
        result["stages"]["corrected"] = "disabled"
    elif transcribe_outcome in {"failed", "blocked"}:
        result["stages"]["corrected"] = "blocked"
    elif transcribe_outcome == "disabled" and not nonempty(paths["raw_text"]):
        result["stages"]["corrected"] = "blocked"
    elif not args.dry_run and not nonempty(paths["raw_text"]):
        result["stages"]["corrected"] = "blocked"
    else:
        result["stages"]["corrected"] = execute_stage(
            "corrected", f"文案纠正 {work_id}", state, state_path, args, logger,
            lambda: runner.run(f"文案纠正 {work_id}", correction_command(config, creator, paths), env),
            (paths["final"], paths["report"]),
        )

    return result


def process_downstream(
    config: dict[str, Any], creator: dict[str, Any], work: dict[str, Any],
    works_file: Path, profile_file: Path | None, state_dir: Path, media_dir: Path,
    runner: Runner, logger: Logger, env: dict[str, str], args: argparse.Namespace,
    result: dict[str, Any],
) -> dict[str, Any]:
    work_id = str(work["aweme_id"])
    state_path = state_dir / creator_key(creator) / f"{safe_key(work_id)}.json"
    state = load_state(state_path, creator, work)
    provider = str(chosen(creator, section(config, "asr"), "provider", "volcengine"))
    paths = artifact_paths(media_dir, work_id, provider)
    correction_outcome = result["stages"].get("corrected", "blocked")
    final_available = (args.dry_run or nonempty(paths["final"])) and correction_outcome not in {"failed", "blocked"}
    if correction_outcome == "disabled":
        final_available = nonempty(paths["final"])
    downstream: list[tuple[str, bool, str, Callable[[], None]]] = [
        (
            "feishu_written_back", args.skip_feishu_writeback, f"回写飞书 {work_id}",
            lambda: runner.run(
                f"回写飞书 {work_id}", writeback_command(config, creator, work, paths), env,
                sensitive=("--table-id", "--base-token"),
            ),
        ),
        (
            "ima_backed_up",
            args.skip_ima or not bool(section(config, "ima").get("enabled", True)),
            f"备份 IMA {work_id}",
            lambda: runner.run(f"备份 IMA {work_id}", ima_command(config, creator, paths), env),
        ),
        (
            "kuake_backed_up",
            args.skip_kuake or not bool(section(config, "kuake").get("enabled", True)),
            f"备份夸克 {work_id}",
            lambda: runner.run(f"备份夸克 {work_id}", kuake_command(config, creator, work, paths), env),
        ),
        (
            "obsidian_exported",
            args.skip_obsidian or not bool(section(config, "obsidian").get("enabled", True)),
            f"备份 Obsidian {work_id}",
            lambda: runner.run(
                f"备份 Obsidian {work_id}",
                obsidian_command(config, creator, work, works_file, profile_file, paths, args.overwrite), env,
            ),
        ),
    ]
    for stage, disabled, label, action in downstream:
        if disabled:
            result["stages"][stage] = "disabled"
        elif not final_available:
            result["stages"][stage] = "blocked"
        else:
            result["stages"][stage] = execute_stage(
                stage, label, state, state_path, args, logger, action,
            )
    return result


def process_work(
    config: dict[str, Any], creator: dict[str, Any], work: dict[str, Any],
    works_file: Path, profile_file: Path | None, state_dir: Path, media_dir: Path,
    runner: Runner, logger: Logger, env: dict[str, str], args: argparse.Namespace,
) -> dict[str, Any]:
    """Run one work end-to-end; retained as the single-worker interface."""
    result = prepare_work(
        config, creator, work, works_file, profile_file, state_dir, media_dir,
        runner, logger, env, args,
    )
    return process_downstream(
        config, creator, work, works_file, profile_file, state_dir, media_dir,
        runner, logger, env, args, result,
    )


def asr_worker_count(config: dict[str, Any], args: argparse.Namespace, work_count: int) -> int:
    configured = args.asr_workers
    if configured is None:
        configured = section(config, "asr").get("max_workers", 4)
    try:
        workers = int(configured)
    except (TypeError, ValueError) as exc:
        raise PipelineError(f"asr.max_workers 必须是整数: {configured}") from exc
    if workers < 1:
        raise PipelineError("ASR 并发数必须至少为 1。")
    return min(workers, max(1, work_count))


def process_creator(
    config: dict[str, Any], creator: dict[str, Any], runner: Runner,
    logger: Logger, env: dict[str, str], args: argparse.Namespace,
) -> dict[str, Any]:
    key = creator_key(creator)
    name = str(creator.get("creator_name") or creator.get("creator_dir_name") or key)
    logger.write(f"========== 达人开始: {name} ({key}) ==========")
    works_file = path_from(creator.get("works_file"), PROJECT_DIR / "runtime" / f"{key}-works-from-mediacrawler.json")
    profile_file = path_from(creator.get("profile_file"))
    media_output = path_from(creator.get("media_output_dir"), PROJECT_DIR / "runtime" / f"mediacrawler-output-{key}")
    if works_file is None or media_output is None:
        raise PipelineError(f"达人 {key} 的路径配置无效。")
    result: dict[str, Any] = {"key": key, "creator_name": name, "works_file": str(works_file), "works": []}

    collection_ok = True
    if args.skip_collect:
        logger.write(f"跳过采集 {name}")
    else:
        try:
            runner.run(
                f"采集 {name}",
                collect_command(config, creator, works_file, media_output, args.normalize_only), env,
            )
        except Exception as exc:
            collection_ok = False
            result["collection_error"] = str(exc)
            logger.write(f"采集失败 {name}: {exc}")
            if args.fail_fast:
                raise

    if not works_file.exists():
        if args.dry_run:
            logger.write(f"作品文件不存在，DRY-RUN 只展示到采集阶段: {works_file}")
            result["status"] = "planned_collection_only"
            return result
        raise PipelineError(f"作品文件不存在: {works_file}")

    all_works = load_works(works_file)
    maximum = args.max_works if args.max_works is not None else int(config.get("max_works") or 0)
    selected = select_works(all_works, set(args.aweme_id), maximum)
    result["selected_count"] = len(selected)
    logger.write(f"{name} 本轮选择 {len(selected)} / {len(all_works)} 条作品")
    state_dir = path_from(config.get("state_dir"), DEFAULT_STATE_DIR) or DEFAULT_STATE_DIR
    media_dir = path_from(config.get("media_dir"), DEFAULT_MEDIA_DIR) or DEFAULT_MEDIA_DIR
    result["backup_mappings"] = sync_creator_backup_mappings(
        config, creator, profile_file, state_dir, runner, logger, env, args,
    )

    if not args.dry_run:
        for work in selected:
            state_path = state_dir / key / f"{safe_key(str(work['aweme_id']))}.json"
            state = load_state(state_path, creator, work)
            inferred = args.skip_collect or not collection_ok
            set_status(
                state, "collected", "success",
                "使用已有作品文件" if inferred else "本轮采集作品文件已加载", inferred,
            )
            write_json(state_path, state)

    sync_ok = True
    if args.skip_feishu_sync:
        logger.write(f"跳过飞书作品同步 {name}")
    else:
        try:
            runner.run(
                f"飞书作品同步 {name}", sync_command(config, creator, works_file), env,
                sensitive=("--table-id", "--base-token"),
            )
        except Exception as exc:
            sync_ok = False
            result["feishu_sync_error"] = str(exc)
            logger.write(f"飞书作品同步失败 {name}: {exc}")
            if args.fail_fast:
                raise

    if not args.dry_run and not args.skip_feishu_sync:
        for work in selected:
            state_path = state_dir / key / f"{safe_key(str(work['aweme_id']))}.json"
            state = load_state(state_path, creator, work)
            set_status(state, "feishu_synced", "success" if sync_ok else "failed", result.get("feishu_sync_error", ""))
            write_json(state_path, state)

    workers = asr_worker_count(config, args, len(selected))
    logger.write(f"{name} ASR/文案纠正并发数: {workers}")
    prepared: dict[str, dict[str, Any]] = {}
    work_by_id = {str(work["aweme_id"]): work for work in selected}
    if workers == 1:
        for work in selected:
            item = prepare_work(
                config, creator, work, works_file, profile_file, state_dir, media_dir,
                runner, logger, env, args,
            )
            prepared[item["aweme_id"]] = item
            if args.fail_fast and "failed" in item["stages"].values():
                raise PipelineError(f"作品 {item['aweme_id']} 存在失败阶段。")
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="asr") as executor:
            futures = {
                executor.submit(
                    prepare_work,
                    config, creator, work, works_file, profile_file, state_dir, media_dir,
                    runner, logger, env, args,
                ): str(work["aweme_id"])
                for work in selected
            }
            for future in as_completed(futures):
                work_id = futures[future]
                try:
                    item = future.result()
                except Exception as exc:
                    logger.write(f"作品预处理异常 {work_id}: {exc}")
                    item = {
                        "aweme_id": work_id,
                        "title": title_of(work_by_id[work_id]),
                        "stages": {"transcribed": "failed", "corrected": "blocked"},
                    }
                prepared[work_id] = item
                if args.fail_fast and "failed" in item["stages"].values():
                    for pending in futures:
                        pending.cancel()
                    raise PipelineError(f"作品 {work_id} 存在失败阶段。")

    # Creator-level mappings and backup destinations are shared resources.
    # Keep delivery serial while the expensive ASR preparation is parallel.
    for work in selected:
        work_id = str(work["aweme_id"])
        work_result = process_downstream(
            config, creator, work, works_file, profile_file, state_dir, media_dir,
            runner, logger, env, args, prepared[work_id],
        )
        result["works"].append(work_result)
        if args.fail_fast and "failed" in work_result["stages"].values():
            raise PipelineError(f"作品 {work_result['aweme_id']} 存在失败阶段。")

    failed_work = any("failed" in item["stages"].values() for item in result["works"])
    mapping_failed = "failed" in result["backup_mappings"].values()
    result["status"] = "partial_failure" if (not collection_ok or not sync_ok or mapping_failed or failed_work) else "success"
    logger.write(f"========== 达人结束: {name}，状态 {result['status']} ==========")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行抖音达人文案完整流水线")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="流水线 JSON 配置，默认 local/pipeline.json")
    parser.add_argument("--creator", action="append", default=[], help="只运行指定达人 key，可重复")
    parser.add_argument("--aweme-id", action="append", default=[], help="只处理指定作品 ID，可重复")
    parser.add_argument("--max-works", type=int, default=None, help="每个达人最多处理条数；0 表示不限")
    parser.add_argument(
        "--asr-workers", type=int, default=None,
        help="视频转音频和 ASR 并发数；默认读取 asr.max_workers，未配置时为 4",
    )
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--normalize-only", action="store_true", help="采集阶段只规范化已有 MediaCrawler 输出")
    parser.add_argument("--skip-feishu-sync", action="store_true")
    parser.add_argument("--skip-transcribe", action="store_true")
    parser.add_argument("--skip-correction", action="store_true")
    parser.add_argument("--skip-feishu-writeback", action="store_true")
    parser.add_argument("--skip-ima", action="store_true")
    parser.add_argument("--skip-kuake", action="store_true")
    parser.add_argument("--skip-obsidian", action="store_true")
    parser.add_argument(
        "--force-stage", action="append", choices=["all", *STAGES], default=[],
        help="强制重跑指定阶段，可重复",
    )
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="忽略成功状态并重跑未禁用阶段")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有 Obsidian 笔记")
    parser.add_argument("--fail-fast", action="store_true", help="任一阶段失败立即停止；默认继续其他备份和作品")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不执行外部命令、不写状态")
    parser.set_defaults(resume=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config_path = path_from(args.config)
        if config_path is None:
            raise PipelineError("配置文件路径无效。")
        config = read_json(config_path)
        creators = config.get("creators")
        if not isinstance(creators, list) or not creators:
            raise PipelineError(f"配置文件没有 creators 数组: {config_path}")
        creators = [item for item in creators if isinstance(item, dict) and item.get("enabled", True)]
        if args.creator:
            requested = set(args.creator)
            creators = [item for item in creators if creator_key(item) in requested]
            found = {creator_key(item) for item in creators}
            if requested - found:
                raise PipelineError(f"配置中找不到或未启用达人: {sorted(requested - found)}")
        if not creators:
            raise PipelineError("没有需要运行的达人。")

        run_id = datetime.now(BEIJING_TZ).strftime("%Y%m%d-%H%M%S")
        log_dir = path_from(config.get("log_dir"), DEFAULT_LOG_DIR) or DEFAULT_LOG_DIR
        logger = Logger(log_dir / f"pipeline-{run_id}.log", persist=not args.dry_run)
        runner = Runner(logger, args.dry_run)
        env = child_env(config)
        logger.write(f"流水线开始，配置={config_path}，dry_run={args.dry_run}")
        summary: dict[str, Any] = {
            "run_id": run_id, "started_at": now_text(), "config": str(config_path),
            "dry_run": args.dry_run, "creators": [],
        }

        for creator in creators:
            try:
                creator_result = process_creator(config, creator, runner, logger, env, args)
            except Exception as exc:
                logger.write(f"达人任务失败 {creator_key(creator)}: {exc}")
                creator_result = {"key": creator_key(creator), "status": "failed", "error": str(exc), "works": []}
                summary["creators"].append(creator_result)
                if args.fail_fast:
                    break
            else:
                summary["creators"].append(creator_result)

        summary["finished_at"] = now_text()
        failed = any(item.get("status") in {"failed", "partial_failure"} for item in summary["creators"])
        summary["status"] = "partial_failure" if failed else ("planned" if args.dry_run else "success")
        if not args.dry_run:
            state_dir = path_from(config.get("state_dir"), DEFAULT_STATE_DIR) or DEFAULT_STATE_DIR
            write_json(state_dir / "runs" / f"{run_id}.json", summary)
        logger.write(f"流水线结束，状态={summary['status']}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    except PipelineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
