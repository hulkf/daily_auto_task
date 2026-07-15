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
- `volcengine_asr.py`: 调用火山引擎 ASR 的本地入口，凭证从环境变量读取。
- `transcribe_douyin_audio_url.py`: 正式转写入口，接收抖音音频 URL，使用临时目录转 WAV 并调用火山 ASR，不保留音视频文件。

火山引擎 ASR 凭证不要写入仓库。建议在本机环境变量或 `douyin_creator_monitor/local/` 下维护：

```powershell
$env:VOLC_ASR_APP_ID = "..."
$env:VOLC_ASR_ACCESS_TOKEN = "..."
$env:VOLC_ASR_CLUSTER = "..."
```

正式流程里不需要保存视频或音频。音频 URL 只作为转写输入，临时文件会在脚本结束后自动删除；后续只把文案或摘要写入飞书。
