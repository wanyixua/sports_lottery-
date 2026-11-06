小王体彩 · 半场/单节盘口（实验版本）

只上传以下3个文件并保持路径：
1) index.html -> 仓库根目录（覆盖）
2) scripts/update_period_markets.py -> scripts/（新增）
3) .github/workflows/update-odds.yml -> .github/workflows/（覆盖，不要改名，否则会创建重复工作流）

上传后在 Actions 手动运行「Update NBA odds cache and publish」。
成功后仓库根目录应生成 period_markets.json，网页可选择全场/半场/单节。
半场包含上、下半场；单节包含第一至第四节；每段包含胜负、让分、大小球。
阶段胜负可能包含平局；下半场合约可能包含加时。具体以 RH 原合同为准。

重要：本版本只使用 RH 公共事件页且必须识别 YES/NO 买入 ask。
由于当前开发环境无法直接访问 RH 的页面源码，已通过语法及模拟 JSON 测试，尚未完成真实页面联调。
RH 更改 Next.js 结构时，分节市场可能只显示链接或暂不可用；不会编造价格。
分节抓取最多每2小时请求一次，和每5分钟的全场赔率分开。
页面为参考报价，不直接进行交易；串关倍率为理论估算。
