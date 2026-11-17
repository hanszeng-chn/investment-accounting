# 数据结构与口径

ledger.json是唯一事实源，format=personal-investment-ledger，当前version=2（可读取v1；首次写入投入核对快照时自动升级并备份），包含accounts、instruments、events、quotes、sources、pending对象数组。金额/份额/价格用十进制字符串，代码字符串保留前导零，日期YYYY-MM-DD。目录另有sources/材料、backups/原账本、dashboard/本地页、public/分享页。写锁ledger.lock异常遗留时先确认无活跃进程再清理。

## 实体

- account：id,name,currency,valuation_mode(manual/holdings),holdings_complete(bool)。只有可靠资料或用户确认覆盖全部持仓才设true。已有事件后不变更币种。

账户可带archived布尔（默认false）、archived_at日期或null，通过skill的account_updates归档/恢复，HTML仅展示数据状态。归档不删除事件，不改变资金流、总资产和历史盈利计算；仅退出当前资产分布，分布占比以未归档账户为分母。历史记录和累计盈利始终保留。
- instrument：id,code,name,kind(LISTED/FUND/OTHER),currency,market(SH/SZ或空)。QDII主题地区不代表计价币种。
- source：id,sha256,filename,path,imported_at。由source命令归档；path不从外部批次任意指定。
- quote：id,instrument_id,date,price,source_id,provider,fetched_at。正价格、未复权实际价或单位净值，不用累计净值。
- pending：id,reason,source_id,extracted，可追加resolved_by关联解决事件，不删除证据。

## 事件

共同字段id,account_id,date,order(同日业务顺序整数),kind,source_id,note。缺少业务顺序时不要用随机ID猜资金边界。更正用supersedes指旧事件，注销kind=VOID、voids指旧事件。被替代者不参与计算，禁止更正分支、循环及跨账户替代。

|kind|字段|含义|
|---|---|---|
|OPENING|amount|期初资产，每账户恰好一条有效记录，不是投入流水|
|CASH_FLOW|amount带符号|外部投入正、取出负；内部买卖不属于外部流。跨账户转账双方各一条可用transfer_id关联|
|VALUATION|amount|含现金的账户总资产，非负|
|POSITION_OPEN|instrument_id,shares|该时点持仓份额基线，后续对账可设新基线，旧交易保留|
|BUY/SELL|instrument_id,shares,amount,fee|确认成交，份额/成交总额正，amount不含fee。买入现金-(amount+fee)，卖出amount-fee，不改变净投入|
|CASH_BALANCE|amount|时点现金核对基线，可负；未知现金不默认为0|
|DIVIDEND_CASH|amount|账户内到账现金分红，不是外部投入；取出另记CASH_FLOW|
|DIVIDEND_REINVEST|instrument_id,shares|确认再投份额，净现金变化0，税费另记FEE|
|SPLIT|instrument_id,shares|已确认折算新增份额，可负非零，不猜精确比例|
|FEE|amount|扣费金额正|
|BASIS_CHECKPOINT|amount,opening_amount,gross_in,gross_out|来源明确的截至日净投入核对快照，须满足amount=opening_amount+gross_in-gross_out。不改变现金或份额，不伪造历史资金流|

申赎/委托未成交暂存pending。应收、复杂负债与外汇暂用人工总资产，不假装支持。缺历史成本不计算税务成本或已实现/未实现损益，初始化0金额绝非成本0。

人工账户取最近OPENING/VALUATION。资金流只计算至估值date/order边界，之后流单列未估值流，期初之前资金流不接受。基数=期初+累计外部净流入，损益=资产-基数，基数非正时简单收益率不适用，不称年化/XIRR/TWR。

v2：存在截至估值边界的BASIS_CHECKPOINT时，采用最后快照的amount，加其后的外部净流入；更早日期仍按当时明细计算，不把未知差额回填成任意日期交易。快照不改变现金、份额或历史记录。原始汇总与明细不一致时保留pending说明差额，待取得明细后再核对。OPENING原为估值起点，不能自动当作用户真实历史投入本金。

holdings账户重放份额和现金。只有holdings_complete、非零持仓都有报价、现金基线已知时才以证券+现金算净资产；否则展示证券估值与缺失项，汇总继续人工值。报价过期/混合日期始终显示，旧份额不能证明无漏记交易。不同币种不相加。

## 增量批次

JSON根对象含上述数组任意几个。先source命令归档得到source_id，再引用该ID。apply --batch <file> --dry-run验证不落盘，去掉dry-run提交。同ID同内容跳过，不同内容报冲突。account_updates显式更新name、valuation_mode、holdings_complete、archived、archived_at。pending_updates用于设置resolved_by。相似经济事件返回duplicate_candidates并拒绝；核实不同成交后显式allow_similar:true并在note说明。

```json
{"events":[{"id":"broker-trade-123","account_id":"broker","kind":"BUY","date":"2026-11-13","order":10,"instrument_id":"SZ:159915","shares":"100","amount":"247.30","fee":"0.10","source_id":"sha256:...","note":"已成交"}]}
```
