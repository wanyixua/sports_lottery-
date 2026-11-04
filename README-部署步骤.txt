小王体彩 · 胜负 + 让分（测试版）

替换 / 上传到 GitHub 仓库根目录，保留下面路径：
  index.html
  odds.json             (不需要覆盖你仓库已更新的 odds.json；仅第一次测试可选)
  spreads.json
  scripts/update_odds.py
  scripts/spread_extract.py
  .github/workflows/update-odds.yml

操作顺序：
1. 上传 index.html、spreads.json，提交。
2. 创建 scripts/spread_extract.py 并替换 scripts/update_odds.py，提交。
3. 更新 .github/workflows/update-odds.yml（保留目录），提交。
4. Actions -> Update NBA odds cache and publish -> Run workflow。
5. 在 Actions 日志中确认 14 games / 868 spread contracts 和报价数。
6. 刷新 GitHub Pages。

注意：随包 spreads.json 是 2026-11-03 的历史快照，不是最新实时报价。
未报价的盘口标记待报价。RH 非官方公共网页数据可能随时改变；页面上的
串关仅按单项 YES 买入价格计算理论倍率，不是实际 Combo 下单价格。

如果部署失败，先检查 .github/workflows/update-odds.yml 和 Actions 日志，
原有网站可用 GitHub 历史版本回滚。
