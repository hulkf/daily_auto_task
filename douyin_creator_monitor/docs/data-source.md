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

