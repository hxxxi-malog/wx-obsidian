# we-mp-rss 派生镜像：仅覆盖一个文件，修正上游两处缺陷。
#
# 缺陷一：发布时间写成抓取时刻（v1.5.3 实测确认）
#   core/wx/model/weread_mp.py 的 _collect_latest_via_cover() 中，
#   create_time / update_time 被硬编码为 int(time.time())，
#   导致 articles.publish_time 等于采集时刻（实测 14 篇的
#   publish_time == create_time == created_at，同秒）。
#   上游自己在同文件 :335-336 的注释里承认「cover 兜底模式会把抓取时间
#   而非文章发布时间写入 update_time」，但只做了规避、没修数据。
#
#   wx-obsidian 侧后果：笔记 frontmatter 的 date 会写成采集日期，
#   且 fetch.max_days 的日期过滤完全失效。
#
#   修法：/web/mp/content 返回的是完整页面（约 3.5MB），内含 `var ct = "<unix秒>"`，
#   即真实发布时间；上游只调用 extract_mp_content() 抠出 #js_content，把外层丢了。
#   补丁在正文获取后回填真实时间 —— 该页面在本路径下**本来就被请求**，
#   因此不增加任何网络请求，也不额外触碰微信风控。
#
# 缺陷二：短链 token 里的 `~`（2026-10-24 实测确认）
#   WeRead 的 reviewId 用 `_` 分隔 bookId 与 token，token 内部的 `_` 被改写成 `~`，
#   而微信短链只认 `_`：`~` 形式打开是「参数错误」页面，正文补抓因此拿不到内容，
#   重试 3 次后文章被标记 FAILED（wx-obsidian 最新文章页显示「无正文」）。
#   实测 16 篇带 `~` 的文章：`~` 形式正文提取 0 字，还原成 `_` 后 16/16 正常。
#
#   修法：build_mp_url / build_mp_link_from_review_id 构造短链前调用
#   restore_short_link_token() 把 `~` 还原成 `_`。历史数据里的旧 URL 需要一次性
#   迁移（见 README「历史数据迁移」）。
#
# ⚠️ 维护提示：patch/weread_mp.py 是从 ghcr.io/rachelos/we-mp-rss:latest (v1.5.3)
#   容器内拷出的整文件副本。升级上游镜像后必须重新导出并重新打补丁，
#   否则会用旧文件覆盖掉上游的新修复。
#   校验方式：docker cp <容器>:/app/core/wx/model/weread_mp.py patch/weread_mp.py
#   然后 diff 确认唯一差异就是本补丁的两处改动。
FROM ghcr.io/rachelos/we-mp-rss:latest

COPY patch/weread_mp.py /app/core/wx/model/weread_mp.py
