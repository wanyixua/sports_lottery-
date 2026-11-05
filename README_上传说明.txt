小王体彩 · 每晚自动结算版（2026-11-04）
=========================================
将以下四个文件按 ZIP 中相同目录结构上传 GitHub 仓库：
  transactions.json                  覆盖（48笔，13笔待结算）
  dashboard.html                     覆盖（支持已命中但金额待补的状态）
  scripts/settle_transactions.py     新增
  .github/workflows/settle-nba-transactions.yml    新增

不需要修改 index.html、odds.json、spreads.json、totals.json 或旧赔率更新 Workflow。

自动执行：每天美国西海岸时间晚上10:00（PDT/PST自动切换），GitHub Actions 实际触发可能延迟。
手动执行：GitHub Actions -> Settle NBA transactions (10 PM PT) -> Run workflow。

如何计算：
  status=pending & ESPN 比赛最终比分可得 -> 逐项检查胜负与让分/受让。
  全部命中 -> payout=stake+profitIfWin，状态 win。
  任一项目失利 -> payout=0，状态 loss。
  尚未结束/暂时抓不到赛果 -> pending，不猜。
  出现走水、退款或特殊规则 -> review，人工核实。
  全部命中但没有 profitIfWin -> win_needs_amount，等待你补充，禁止虚构收益。

本批次已确认命中净赚：
  #36 6串1 $30赚$120
  #37 PHI+14.5 $50赚$6
  #38 ATL+6.5 $20赚$7
  #39 CHA+5.5 $20赚$5
  #40 NYK-5.5 $20赚$6
  #41—#47 使用先前微信截图确认的对应净赚金额
  #48 小王 4串1 $30，命中收益暂未知

注意：
  1. 使用 ESPN 公开非正式 API；接口可能改变或暂时不可用。没有确认比分不会改变待结算记录。
  2. 仅按 NBA 标准最终比分算普通胜负、让分、受让；目前的 13 笔均没有大小分投注。
     settle_transactions.py 已包含大小分判断接口，但自动解析旧订单字符串只支持胜负/让分；
     未来大小分需保存结构化 legs [{"team":"BOS","market":"total","side":"over","line":220.5}]。
  3. 比赛取消、推迟、推注与 RH 特殊合约结算请人工核实。
  4. GitHub Pages 公开站点中的交易 JSON 仍公开，原前端密码仅是浏览门槛。
  5. 必须检查仓库 Settings -> Actions -> General -> Workflow permissions 允许 Read and write permissions，
     并确保 Settings -> Pages 已配置通过 GitHub Actions 发布。
  6. 只使用此文件夹的四个正式文件。测试 fixtures 不应上传。
