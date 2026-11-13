
## 命令

```bash
./run.sh --dry-run      # 只打印将推送的内容，不推送、不写 state
./run.sh --init         # 只把 709 条历史底库灌入 state（首次部署用）
./run.sh --test         # 发一条自检消息，验 ntfy 链路
./run.sh                # 正常跑一轮
./run.sh --max 1        # 覆盖单轮推送上限
```
