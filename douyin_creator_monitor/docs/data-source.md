# Data Source Notes

## 抖音主页作品列表接口

当前验证可用的数据来源是达人主页加载时请求的列表接口：

```text
/aweme/v1/web/aweme/post/
```

该接口需要在已登录 Chrome 页面中由抖音前端生成签名参数后读取。已观察到的关键参数包括：

- `sec_user_id`
- `max_cursor`
- `count`
- `need_time_list`
- `a_bogus`
- `x-secsdk-web-signature`

## 作品字段映射

- `aweme_id`: 抖音作品 ID
- `desc`: 原始文案
- `create_time`: 发布时间，Unix 秒级时间戳，写入飞书前转为北京时间
- `statistics.digg_count`: 点赞数
- `statistics.comment_count`: 评论数
- `statistics.collect_count`: 收藏数
- `statistics.share_count`: 分享数
- `video.duration`: 视频时长，通常为毫秒
- `video.cover.url_list[0]`: 封面图
- `is_top`: 是否置顶

## 注意事项

- 不逐个打开作品详情页获取发布时间。
- 详情页里曾观察到过不可信的固定日期字段，不作为发布时间来源。
- 主页卡片可见数字已确认对应 `statistics.digg_count`，即点赞数。
- 测试流程可只写入少量样本作品；正式初始化再做分页全量抓取。

## 已验证：从现有 Chrome 页面定位完整签名请求

2026-07-16 在已登录的 Chrome 中重新验证了下列方法。该方法不需要手动拼接 `a_bogus`、`msToken` 或 `x-secsdk-web-signature`，也不需要逐个打开作品详情页。

1. 在已登录 Chrome 中打开目标达人主页。
2. 连接目标标签页后执行页面重载，等待“作品 N”标签出现，确认作品列表已加载。
3. 读取当前页面已观测到的资源清单（Chrome 控制器的 `pageAssets.list()`）。
4. 在资源 URL 中精确筛选 `/aweme/v1/web/aweme/post/`。
5. 使用资源清单返回的完整 URL，不要自行重签名或改变参数顺序。

已确认完整请求 URL 包含：

- `sec_user_id`
- `max_cursor`
- `count`
- `need_time_list=1`
- `msToken`
- `a_bogus`
- `timestamp`
- `x-secsdk-web-signature`

重要细节：

- 资源清单只保留当前标签页已观测到的请求。如果第一次搜索不到 `aweme/post`，先重载达人主页，再重新读取资源清单。
- 签名 URL 属于短期运行产物，只能临时保存在 `runtime/`，不得写入可提交文档，不得提交到 Git。
- 直接在新标签页导航到该 API 可能被 Chrome 客户端拦截。
- 将签名 URL 脱离浏览器会话后用普通 HTTP 客户端重放，已观察到 `HTTP 200` 但 `Content-Length: 0`。这表明仅有签名 URL 不足以获取响应，还必须保持原 Chrome 会话上下文。

## 已验证：Crawlio 捕获接口响应 body

2026-07-16 已在用户已登录的 Chrome 标签页中验证成功。目标主页为：

```text
https://www.douyin.com/user/MS4wLjABAAAAoePkj5ldelmgGm4fSjvGmaayTHyvuwq6XIz_1Occ9uc?from_tab_name=main
```

成功方法不是把签名 URL 拿到浏览器外重放，也不是在页面里直接 `fetch` 资源表中的 URL；这两种方式都可能返回 HTML。可靠方法是在页面真正触发接口前注入 `fetch`/`XMLHttpRequest` 响应体钩子，然后让抖音前端自己重新请求 `/aweme/v1/web/aweme/post/`。

操作顺序：

1. 用 Crawlio `list_tabs` 找到目标 Chrome 标签页。
2. 用 `connect_tab` 连接目标标签页。
3. 用 `browser_snapshot` 确认页面显示 `知了-千川推商品` 和 `作品 43`。
4. 用 `browser_evaluate` 在页面上下文注入钩子：
   - 初始化 `window.__capturedAwemePostBodies = []`。
   - 包装 `window.fetch`，命中 `/aweme/v1/web/aweme/post/` 时对 `Response.clone().text()` 保存响应体。
   - 包装 `XMLHttpRequest.prototype.open/send`，命中 `/aweme/v1/web/aweme/post/` 时在 `load` 事件中保存 `responseText`。
5. 触发页面重新请求作品列表。实测可通过切换主页上方 `推荐` 标签，再回到作品区域，或刷新有效的 `?from_tab_name=main` 标签页触发。
6. 用 `browser_evaluate` 读取并解析 `window.__capturedAwemePostBodies`。
7. 校验每页 JSON 包含 `aweme_list`、`has_more`、`max_cursor`，按 `aweme_id` 去重。

本次抓取结果：

- 共捕获 3 个 `/aweme/v1/web/aweme/post/` XHR 响应。
- 分页数量为 `18 + 18 + 7`。
- 去重后当前可见作品数为 `43`。
- 三页 `has_more` 依次为 `1, 1, 0`。
- 43 条作品的核心字段均完整：`aweme_id`、`create_time`、`statistics.digg_count`、`statistics.comment_count`、`statistics.collect_count`、`statistics.share_count`。

核心字段映射：

```text
aweme_id -> 抖音作品ID
desc -> 原始文案 / 作品标题 / 话题标签
create_time -> 发布时间（Unix 秒，写入前转北京时间）
statistics.digg_count -> 点赞数
statistics.comment_count -> 评论数
statistics.collect_count -> 收藏数
statistics.share_count -> 分享数
video.duration -> 视频时长（毫秒；若飞书表暂无该字段则不写）
video.cover.url_list[0] -> 封面图URL
is_top -> 是否置顶
```

注意事项：

- `performance.getEntriesByType("resource")` 可以拿到完整签名请求 URL，但只能作为定位线索；不要把其中的 `msToken`、`a_bogus`、`x-secsdk-web-signature` 写入可提交文档。
- Crawlio `start_network_capture` 能确认接口请求出现，但本环境中 `get_response_body` 对该 URL 返回过插件内部错误；实际成功路径是页面内 XHR/fetch 钩子。
- 直接在页面上下文 `fetch(签名 URL, { credentials: "include" })` 返回过 HTML，说明原始请求还依赖前端 XHR/SDK 上下文或请求头，不应作为正式方案。
- 原始响应和签名 URL 只能放在 `runtime/`，不得提交。
- 写入飞书前必须先校验核心字段完整；任一当前作品缺少发布时间或互动数据，都不能标记为已完成。
