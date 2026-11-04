小王体彩 - 让分(-) + 受让(+) 升级包

将以下文件按相同路径上传/覆盖到现有 GitHub 仓库：
  index.html
  scripts/spread_extract.py
  scripts/update_odds.py

其余文件（包括 champions.json、totals.json、冠军解析脚本、GitHub Actions 工作流）不需要修改。
上传并 Commit 后，进入 GitHub Actions 手动运行 Update NBA odds cache and publish 一次。
运行成功后，新的 spreads.json 会自动生成，让分与受让两侧报价同时显示。

技术说明：
- 每份 NBASPREAD 合约的 YES 侧 = 原让分球队 -X.5。
- 同一份合约的 NO 侧 = 对手受让 +X.5。
- 读取独立的 yes_ask_price 和 no_ask_price，不通过 1 - YES 推算。
- 每场依然只能选择一个盘口；购物车支持两个方向。
- 无报价项显示“待报价”，无须购买/交易权限。
- 测试使用此前保存的2026-11-03 RH网页快照，并非实时行情。
