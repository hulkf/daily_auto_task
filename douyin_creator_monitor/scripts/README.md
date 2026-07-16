# Scripts

这里用于放后续沉淀的可复用脚本。

当前项目还处在流程验证阶段，实际执行主要依赖：

- Chrome 登录态
- 页面接口观察
- `tools/lark-cli/lark-cli.exe`
- `douyin_creator_monitor/runtime/` 中的 JSON 模板和测试产物

后续建议沉淀的脚本：

- `sync_new_creator.ps1`: 读取达人基础信息表中的新增主页链接，抓取基础信息，新建作品表并写入首批作品。
- `sync_creator_works.ps1`: 对已有达人检查新增作品并追加到对应作品表。
- `extract_post_list.ps1`: 通过浏览器上下文读取抖音主页作品列表接口。
- `transcribe_with_tingwu.ps1`: 下载音频并对接通义听悟网页转写流程。

已开始沉淀的脚本：

- `download_douyin_media.py`: 使用已签名的抖音视频/音频 URL 下载样本，并用 FFmpeg 生成 16k 单声道 WAV。
- `volcengine_asr.py`: 调用火山引擎 ASR 的本地入口，凭证从环境变量或本地忽略配置读取。
- `bailian_paraformer.py`: 调用阿里云百炼/DashScope Paraformer 的本地入口，凭证从环境变量读取。
- `transcribe_douyin_audio_url.py`: 正式转写入口，接收抖音音频 URL，默认使用火山引擎；百炼 Paraformer 仅作为低成本备选。
- `feishu_transcript_writer.py`: 按 `抖音作品ID` 定位飞书作品表记录，并把词库纠正后的最终文案写回 `语音转写全文` 字段。

火山引擎凭证不要写入仓库。建议在本机环境变量或 `douyin_creator_monitor/local/volcengine.env.json` 下维护：

```powershell
$env:VOLC_ASR_APP_ID = "..."
$env:VOLC_ASR_ACCESS_TOKEN = "..."
$env:VOLC_ASR_CLUSTER = "..."
```

火山 AppID/Access Token 已验证需要使用 `Authorization: Bearer; token` 的格式。若返回 `requested resource not granted`，说明当前应用未开通对应语音识别资源，或 `VOLC_ASR_CLUSTER` 与控制台授权资源不匹配。

百炼/DashScope 如果需要保留对比，也可在本机环境变量或 `douyin_creator_monitor/local/bailian.env.json` 下维护：

```powershell
$env:DASHSCOPE_API_KEY = "sk-..."
$env:BAILIAN_ASR_MODEL = "paraformer-v2"
```

脚本会自动读取被 Git 忽略的本地配置文件。

正式流程里不需要保存视频或音频。音频 URL 只作为转写输入：

1. `auto` 模式先把音频 URL 交给火山引擎直接读取。
2. 如果火山读取不了 URL，再兜底走本机临时下载、FFmpeg 转 16k WAV、直接上传给火山 ASR。
3. 如果显式使用 `--provider bailian`，百炼兜底才需要上传到临时对象存储，拿到公网 URL 后再次提交百炼。
4. 临时文件会在脚本结束后自动删除；后续只把文案或摘要写入飞书。

下载兜底需要配置上传命令，命令中用 `{file}` 表示临时 WAV 文件路径，并把公网 URL 输出到 stdout：

```powershell
$env:BAILIAN_UPLOAD_COMMAND = "your-upload-command --file {file}"
```

## IMA 备份脚本

- `backup_transcripts_to_ima.py`: 把本地 `.txt` 文案按“博主 -> IMA 知识库/文件夹”映射备份到腾讯 IMA。真实凭证和映射文件放在 `douyin_creator_monitor/local/`，提交到 Git 的只有模板和说明。
