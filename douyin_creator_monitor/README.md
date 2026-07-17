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
