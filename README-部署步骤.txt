NBA Odds 自动发布准备版（仍未接通 RH 自动数据采集）

一、上传 GitHub
把 index.html、odds.json、scripts/update_odds.py、.github/workflows/update-odds.yml 放到仓库相同路径。
GitHub 网页上传时要保留子目录路径，最简单办法是本地解压后 git add/commit/push。

二、GitHub -> Settings -> Pages
把 Source 从 "Deploy from a branch" 改成 "GitHub Actions"。
这一步很重要：定时 workflow 自己负责发布 Pages，避免 GITHUB_TOKEN push 不触发分支构建。

三、GitHub -> Actions -> Update NBA odds cache and publish -> Run workflow
运行成功后站点仍展示原有 5 场截图缓存，因为未设置数据源。

四、接入真实数据
需要一个允许被访问、输出与 odds.json 相同结构且有合法访问/再分发权限的 HTTPS JSON 数据源。
在 Settings -> Secrets and variables -> Actions -> Variables 中创建 ODDS_SOURCE_URL 变量。
之后定时任务约每15分钟抓取一次、校验、写入 odds.json 并部署。
当前并未核实 Robinhood 提供可用官方赔率 API，不得将其他来源标为 RH 原始赔率。

注意：GitHub Actions 定时触发可能延迟；GitHub Pages 中国大陆访问不保证稳定；报价不含交易手续费。
