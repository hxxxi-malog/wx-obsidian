# we-mp-rss 派生镜像：仅覆盖一个文件，修正「发布时间 = 抓取时间」的上游缺陷。
#
# 上游缺陷（v1.5.3 实测确认）：
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
# 修法：/web/mp/content 返回的是完整页面（约 3.5MB），内含 `var ct = "<unix秒>"`，
#   即真实发布时间；上游只调用 extract_mp_content() 抠出 #js_content，把外层丢了。
#   补丁在正文获取后回填真实时间 —— 该页面在本路径下**本来就被请求**，
#   因此不增加任何网络请求，也不额外触碰微信风控。
#
# ⚠️ 维护提示：patch/weread_mp.py 是从 ghcr.io/rachelos/we-mp-rss:latest (v1.5.3)
#   容器内拷出的整文件副本。升级上游镜像后必须重新导出并重新打补丁，
#   否则会用旧文件覆盖掉上游的新修复（例如上游对短链 token 中 `~` 的处理变更）。
#   校验方式：docker cp <容器>:/app/core/wx/model/weread_mp.py patch/weread_mp.py
#   然后 diff 确认唯一差异就是本补丁。
FROM ghcr.io/rachelos/we-mp-rss:latest

COPY patch/weread_mp.py /app/core/wx/model/weread_mp.py
