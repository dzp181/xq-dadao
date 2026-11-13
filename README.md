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

## 定时

任选其一，30～60 分钟一轮就够（他发言低频，一天 0～2 条）：

```bash
# 国内服务器 crontab（推荐，与 x2ding 同一台）
*/30 * * * * cd /path/to/xq-dadao && ./run.sh >> /tmp/xq-dadao.log 2>&1

# 本机 macOS crontab
*/30 * * * * cd /Users/you/xq-dadao && ./run.sh >> /tmp/xq-dadao.log 2>&1
```

`.github/workflows/xq.yml` 提供 GitHub Actions 版本（`workflow_dispatch` + `schedule`），
需要 secret `NTFY_TOKEN`。**境外 IP 是否能走通这条链路尚未验证**，优先用国内服务器。

## 边界与风险

- 雪球若调整 WAF 或匿名会话策略，链路可能失效；脚本会明确报错并保持 state 不变，不会误推。
- 只取用户自己时间线上的动态，**不含**"他在别人帖子评论区里、没有同步进时间线"的散落评论。
- 付费内容、仅好友可见、已删除的帖子拿不到。
- 只做个人订阅、低频、不分发；不做任何绕过 WAF / 破解 JS 加密的动作。
- 令牌只写进 `local.env` 或 CI secret，不要提交到仓库。
