# xq-dadao —— 段永平雪球动态 → ntfy 推送

独立项目，与 `x2ding` / `xadmin` 完全隔离：独立目录、独立配置、独立 `state.json`、独立 ntfy 主题。
雪球接口失效或本脚本挂掉，不会影响现有 X 监控。

## 订阅

| 项 | 值 |
| --- | --- |
| 订阅地址 | `http://112.74.95.74:8858/x-dadao` |
| 主题 | `x-dadao` |

iOS ntfy App：`Settings → Subscribe to topic`，URL 填订阅地址。
主题必须以 `x-` 开头——现有发布令牌的授权范围只有 `x-*`，用 `xq-` 之类会被 403 拒绝。

## 数据源：无需登录、无需 cookie、无需浏览器

2026-11-13 实测链路，纯 HTTP、零第三方依赖：

```
GET https://xueqiu.com/1247347556              <- 别名路径
  → 200 真实主页 HTML，下发 u / cookiesu 两个匿名 cookie

GET https://api.xueqiu.com/v4/statuses/user_timeline.json
      ?user_id=1247347556&type=10&page=1
  带上一步 cookie + Referer + Accept: application/json
  → 200，20 条完整时间线，含回复上下文
```

**关键差别**：`xueqiu.com/u/1247347556` 会撞阿里云 WAF；
`xueqiu.com/1247347556`（不带 `/u/`）不会。
`api.xueqiu.com` 裸调会返回 `400 请刷新页面或重新登录`，但带上别名页的两个匿名 cookie 就通。
实测 3/3 轮成功，20 条里 20 条都带 `retweeted_status`，最新一条是当轮几分钟前。

`type=10` 是「全部动态」，所以主帖、长文、转发、**回复**都在一条时间线里。
回复的 `description` 本身就带完整对话链（`回复@A: … //@B:回复@C: …`），
脚本直接保留，不做 HTML 解析丢失上下文；只有没有引用链时才用 `retweeted_status` 补原帖。
分页是 `page` / `maxPage`（实测 `maxPage=623`），不是 `max_id`。

## 消息格式

与 x2ding 一致：一条动态 = 一条通知。

- 标题：`段永平 · 发帖 / 回复 / 转发 / 长文`
- 正文：全文 +（必要时）被回复原帖上下文 + 来源 + `YYYY-MM-DD HH:mm CST` 时间行
- 链接走 `click`，正文里不放网址，避免 iOS 折行乱版
- 长文被截断时补一次 `/statuses/show.json` 拿全文

## 命令

```bash
./run.sh --dry-run      # 只打印将推送的内容，不推送、不写 state
./run.sh --init         # 只把 709 条历史底库灌入 state（首次部署用）
./run.sh --test         # 发一条自检消息，验 ntfy 链路
./run.sh                # 正常跑一轮
./run.sh --max 1        # 覆盖单轮推送上限

python3 xq_push.py --import-raw data/seed_timeline.json --online
                                               # 从原始抓取快照重建归档（联网补长文全文）
python3 export_ima.py                          # 生成可导入 ima 的 Markdown 快照
```

令牌放在 `local.env`（不入库）：

```bash
echo 'XQ_NTFY_TOKEN=tk_xxxxxxxx' > local.env
chmod 600 local.env
```

## 首次运行不会刷屏

`baseline_ids.txt` 是从公开归档
[ai-berkshire 段永平雪球发言索引](https://github.com/xbtlin/ai-berkshire)（709 条，截至 2026-10-21）
提取的历史 status_id。首次运行时全部标记为已见，只推最新 1 条做种子，
之后按 `max_per_run`（默认 3）增量推送；超过 `max_age_hours`（默认 18）的旧帖跳过。
推送失败的那条不写进 state，下一轮自动重试，不会漏推。

## 定时（已上线，无需你做任何事）

**默认走 GitHub Actions**：私有仓库 `dzp181/xq-dadao`，`.github/workflows/xq.yml`
每 30 分钟自动跑一轮（`schedule`），也可手动 `gh workflow run xq.yml -f dry_run=true`。
2026-11-13 实测境外 Actions IP 能直连别名主页 + `api.xueqiu.com`，
日志确认「匿名会话已建立（title=大道无形我有型 - 雪球）」。
secret `NTFY_TOKEN` 已设置，本地 `local.env` 只用于手动跑。

CI 每轮是干净环境，所以 workflow 在推送后把 `state.json` 提交回仓库，
避免新帖被重复推送；没有新动态时脚本不写 state，也就不会产生多余 commit。

代价：GitHub 对 60 天无活动的仓库会停用 `schedule`。若发现停更，用下面任一方式兜底
（与 x2ding 同一台服务器，或由服务器 crontab 调 `workflow_dispatch`）：

```bash
# 国内服务器 crontab（备选）
*/30 * * * * cd /path/to/xq-dadao && ./run.sh >> /tmp/xq-dadao.log 2>&1

# 本机 macOS crontab（备选）
*/30 * * * * cd /Users/you/xq-dadao && ./run.sh >> /tmp/xq-dadao.log 2>&1

# 或由服务器敲一下 Actions（与 x2ding 的模式一致）
gh workflow run xq.yml -R dzp181/xq-dadao
```

## 数据归档与 ima 知识库

除了 ntfy 推送，抓到的**内容全文**会另存一份，供知识库检索与问答。

| 路径 | 内容 |
| --- | --- |
| `archive/items.jsonl` | 机器可读全量归档，一行一条（`id/kind/title/text/context/url/ts/来源/互动数`），按 id 去重 |
| `archive/by-month/YYYY-MM.md` | 按月的人可读版本 |
| `data/history_index.json` | 709 条历史发言索引精简版（来自 ai-berkshire 公开归档，只有摘要与链接） |
| `data/seed_timeline.json` | 一次原始 `user_timeline.json` 抓取的精简快照，用于可复现地重建归档 |
| `ima_export/段永平雪球动态.md` | **实时全量快照**，固定文件名，`export_ima.py` 生成，不入库 |
| `ima_export/段永平雪球发言历史索引.md` | 历史索引快照，一次入库即可 |

归档与推送是解耦的：`xq_push.py` 抓到时间线后先写归档，再走推送逻辑。
所以即使某条推送失败、或超过 `max_per_run` 没推，正文仍完整留存。
归档失败不影响推送，dry-run 不落盘。
CI 的 `Persist state and archive` 步骤把 `state.json` 与 `archive/` 一起提交回仓库，
所以每 30 分钟的新内容会自动沉淀在私有仓库里。

**ima 存放位置**：知识库「美股投研小白知识库」→ 文件夹「网红人物帖推送」→「段永平」。
2026-11-13 已导入两份文档并验证正文可读：实时动态全文（20 条）与历史索引（709 条）。

**为什么不是 CI 直接写 ima**：ima 只能通过 WorkBuddy 的 ima 连接器访问，
GitHub Actions 里跑的 Python 脚本拿不到这个凭证，也不该把个人知识库令牌放进 CI。
所以链路是「CI 沉淀 `archive/` → 本机 `python3 export_ima.py` → 在 WorkBuddy 会话里说一句
『把段永平数据同步到 ima』完成上传」。增量更新用**同名替换**：`段永平雪球动态.md`
文件名固定，重新生成后覆盖同名条目即可，不会在文件夹里越堆越多；
历史索引是静态的，只在归档来源变更时才需要重传。

## 边界与风险

- 雪球若调整 WAF 或匿名会话策略，链路可能失效；脚本会明确报错并保持 state 不变，不会误推。
- 只取用户自己时间线上的动态，**不含**"他在别人帖子评论区里、没有同步进时间线"的散落评论。
- 付费内容、仅好友可见、已删除的帖子拿不到。
- 只做个人订阅、低频、不分发；不做任何绕过 WAF / 破解 JS 加密的动作。
- 令牌只写进 `local.env` 或 CI secret，不要提交到仓库。
