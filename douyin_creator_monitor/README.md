# Douyin Creator Monitor

这个目录用于管理“抖音达人日常作品监控”项目的脚本、说明和运行产物。

项目目标：

- 每天定时检查一批抖音达人主页是否有新作品。
- 从达人主页列表接口获取作品文案、发布时间、点赞数、评论数、收藏数、分享数等字段。
- 按“一个达人一个作品表”的规则写入飞书多维表格。
- 在达人基础信息表中维护达人资料、最近发稿时间、作品表名称、作品表 ID 和作品表链接。
- 后续再接入通义听悟转写、ima、百度网盘、本地知识库等下游流程。

## 当前状态

当前主链路：

1. 从飞书“达人基础信息表”读取新增达人主页链接。
2. 调用本机 MediaCrawler 项目框架抓取抖音达人作品。
3. 将 MediaCrawler 导出的作品数据规范化为本项目统一 JSON：`runtime/zhiliao-works-from-mediacrawler.json`。
4. 新建该达人专属作品表。
5. 用 `抖音作品ID` 做唯一键写入或覆盖更新作品记录。
6. 回填达人基础信息表中的基础字段和作品表关联字段。

历史上用 Chrome/Crawlio 验证过 `/aweme/v1/web/aweme/post/` 响应捕获方法，这套方法保留在 `docs/data-source.md` 作为排障兜底，不再作为默认采集底层。

## 飞书 Base 信息

飞书 Base token、table ID、view ID、wiki 链接等属于本地私有配置，不写入可提交文档。

本地配置建议放在：

```text
douyin_creator_monitor/local/feishu-ids.md
```

该路径已被 `.gitignore` 忽略，后续推送 GitHub 时不会提交。

## 目录约定

- `runtime/`: MediaCrawler 导出、浏览器抓取、飞书写入测试过程中产生的 JSON、JS、二维码、接口样本等运行产物。
- `docs/`: 项目说明、字段说明、接口观察结论。
- `scripts/`: 后续沉淀的可复用脚本。当前主要流程仍是手动验证和 CLI 命令组合。

从现在开始，这个项目新增的脚本、说明、模板、测试 JSON 和临时产物都放在 `douyin_creator_monitor/` 目录下，不再散落到仓库根目录。

## IMA 文案备份

火山 ASR 得到的 `.txt` 文案可以先保存到本地，再用 `scripts/backup_transcripts_to_ima.py` 备份到腾讯 IMA。博主和 IMA 知识库/文件夹的映射关系放在 `douyin_creator_monitor/local/ima_creator_mapping.json`，模板见 `config/ima_creator_mapping.example.json`。具体步骤见 `docs/ima-backup.md`。

## 夸克网盘文案备份

夸克网盘 CLI 已按本项目约定安装到本地 `tools/kuake-cli/`，真实登录态放在 `douyin_creator_monitor/local/kuake.env.json`。火山 ASR 得到的 `.txt` 文案可用 `scripts/backup_transcripts_to_kuake.py` 上传到指定夸克目录，默认按 `/视频文案备份/博主名/日期_视频ID_标题.txt` 组织。具体步骤见 `docs/kuake-backup.md`。


## 完整自动化流水线

统一入口：

~~~text
douyin_creator_monitor/scripts/run_creator_pipeline.py
~~~

它按以下顺序复用现有模块，不复制各平台的底层实现：

1. MediaCrawler 采集并规范化达人作品。
2. 按统一作品表字段契约校验结构，用「抖音作品ID」分页识别已有作品；批量新增、仅更新内容变化的记录，不删除历史记录。
3. 从作品数据读取 music_download_url，调用火山 ASR（或配置的其他 ASR）。
4. 按达人配置的领域词库执行文案纠正。
5. 把纠正后的全文回写飞书对应作品记录。
6. 优先复用 IMA、夸克网盘和 Obsidian 的达人目录映射缓存；缓存缺失、过期或显式刷新时并行确认目录，不存在时自动创建。
7. 合并检查三个平台的目录名称、ID（平台提供时）和路径，一次回写飞书达人基础信息表的差异字段。
8. 在飞书文案回写完成后，受控并行备份到 IMA、夸克网盘和 Obsidian；三个备份阶段失败互不影响。

视频转音频、火山 ASR 和文案纠正按作品受控并发，默认并发数为 4。IMA、夸克和 Obsidian 默认最多 3 路独立备份并发；飞书同表写入保持串行或批量，避免共享记录竞争。每条作品使用独立临时目录、产物文件和状态文件。

### 配置文件

可提交的模板：

~~~text
douyin_creator_monitor/config/pipeline.example.json
~~~

本机实际配置：

~~~text
douyin_creator_monitor/local/pipeline.json
~~~

本地配置已被 .gitignore 忽略，可填写真实的达人主页、飞书作品表 ID、MediaCrawler 路径和本地工具路径。飞书 Base token、IMA 凭证、夸克 Cookie 等仍放在原有环境变量或 local 私有文件中，不要直接写进可提交模板。

`feishu.creator_table_id` 必须配置为达人基础信息表 ID。流水线默认从 `creator_url` 提取 `SecUID` 定位达人记录；特殊达人可以额外设置 `feishu_match_field` 和 `feishu_match_value`。

每个达人至少配置：

- key：命令行选择达人时使用的稳定标识。
- creator_url：抖音达人主页或 SecUID。
- creator_name：飞书、IMA 使用的达人显示名称。
- creator_dir_name：夸克和 Obsidian 使用的目录名称。
- works_table_id：该达人的飞书作品表 ID。
- works_file：规范化作品 JSON 的输出位置。
- profile_file：Obsidian 顶部基础信息所需的达人资料，可选。
- correction_domain：如 douyin_shop_ads 或 ai_media。

全局并发数可在 `asr.max_workers` 中配置。火山账号配额较低或本机需要同时执行 FFmpeg 转码时，可以先设为 2；网络和配额稳定后再逐步提高。命令行 `--asr-workers` 会临时覆盖配置文件。

备份并发数通过 `backups.max_workers` 配置，命令行 `--backup-workers` 可临时覆盖。达人目录映射默认缓存 24 小时，由 `backups.mapping_cache_ttl_hours` 控制；需要立即重新确认远端目录时使用 `--refresh-mappings`。

### 运行命令

先检查单条作品的完整执行计划，不访问外部服务、不写流水线状态：

~~~powershell
python .\douyin_creator_monitor\scripts\run_creator_pipeline.py --creator zhiliao --aweme-id 7661192591962017065 --skip-collect --dry-run
~~~

运行全部已启用达人：

~~~powershell
python .\douyin_creator_monitor\scripts\run_creator_pipeline.py
~~~

只运行一个达人，最多选择最新 3 条：

~~~powershell
python .\douyin_creator_monitor\scripts\run_creator_pipeline.py --creator zhiliao --max-works 3
~~~

临时使用 6 路 ASR 并发：

~~~powershell
python .\douyin_creator_monitor\scripts\run_creator_pipeline.py --creator zhiliao --asr-workers 6
~~~

只规范化已经存在的 MediaCrawler 输出，不重新启动抓取：

~~~powershell
python .\douyin_creator_monitor\scripts\run_creator_pipeline.py --creator zhiliao --normalize-only
~~~

临时关闭某些步骤：

~~~powershell
python .\douyin_creator_monitor\scripts\run_creator_pipeline.py --skip-ima --skip-kuake
~~~

### 断点续跑和幂等

每条作品的阶段状态保存在：

~~~text
douyin_creator_monitor/runtime/pipeline/<达人key>/<作品ID>.json
~~~

单次运行摘要保存在：

~~~text
douyin_creator_monitor/runtime/pipeline/runs/<运行时间>.json
~~~

日志保存在：

~~~text
douyin_creator_monitor/logs/pipeline-YYYYMMDD-HHMMSS.log
~~~

默认会跳过已经成功的逐作品阶段，并复用已有的原始转写和纠正后文案。飞书同步会分页读取全部作品，但只更新内容发生变化的记录；新作品按每批最多 200 条批量创建，并把返回的 `record_id` 直接传给文案和状态回写。需要强制重跑某一步时使用：

~~~text
--force-stage transcribed
--force-stage corrected
--force-stage obsidian_exported --overwrite
--force-stage all
~~~

IMA 默认使用 on_duplicate=skip，避免同名文案重复上传。目录确认和映射检查属于达人级缓存步骤，不会随作品数量重复检查；缓存刷新时三个平台并行确认，并合并为一次飞书差异写入。夸克和 Obsidian 是否已成功以本地阶段状态为准；状态已成功时不会重复远程写入。

每个外部命令的毫秒级耗时、状态、失败调用数以及整轮墙钟时间都会写入运行摘要的 `timings` 和 `wall_seconds`；单作品阶段状态同时保存 `duration_seconds`，便于持续比较优化效果。

单条作品的转写或某个备份失败时，流水线会保留已经成功的结果，并继续其他备份和后续作品。只要最终存在任一失败阶段，进程退出码就是 1，便于 Windows 任务计划程序识别失败。需要遇错立即停止时增加 --fail-fast。

### Windows 任务计划程序入口

程序/脚本填写本机 Python，例如：

~~~text
D:\Anaconda\python.exe
~~~

参数填写：

~~~text
D:\JR_project\daily_auto_task\douyin_creator_monitor\scripts\run_creator_pipeline.py
~~~

起始于填写：

~~~text
D:\JR_project\daily_auto_task\douyin_creator_monitor
~~~

建议先手动运行单条作品并确认飞书、IMA、夸克和 Obsidian 均正确，再接入每日调度。
