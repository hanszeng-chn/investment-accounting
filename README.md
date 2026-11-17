# Netlify 投资账本

此独立分支仅维护 Netlify 投资看板、账本计算和数据同步代码，不包含移动端应用及个人账本数据。

## 数据与运行方式

Netlify Blobs 的加密 `investment-dashboard/current` 是唯一事实来源。本地 `data/personal-investment/` 仅存工作副本、备份和分析导出，不进入 Git。每日北京时间 08:00 更新上一日截至的最新已公布价格。函数通过 Pyodide 运行同一套 Python 估值及收益算法。

- `ledger/`：Python 账本、收益算法、HTML 模板及口径说明。
- `netlify/`：每日更新、密文读取函数和版本保护。
- `tools/`：数据同步、加密、备份、分析和部署命令。

## 安装与验证

需要 Node.js 20+、Python 3 和已经登录的 Netlify CLI。

```bash
npm ci
node --test tools/encrypted-dashboard/build.test.mjs
```

当前站点配置在同步与部署脚本中；部署其他站点时应替换 siteID。授权信息读取本机 Netlify CLI 登录配置，不进入仓库。

## 首次使用本机工作副本

将现有看板密码保存到 `data/personal-investment/secrets/dashboard-password.txt`，权限设置为 600。不要在命令行、Git 或日志中记录密码。站点环境变量 `DASHBOARD_PASSWORD` 必须与它匹配。已有正式账本禁止重新初始化或以旧副本覆盖。

```bash
node tools/ledger-cloud.mjs pull
node tools/ledger-cloud.mjs export
node tools/ledger-cloud.mjs analyze
```

`analyze` 导出完整 JSON 和六张 CSV，属于明文个人数据，仅保留本机。`export` 生成独立加密备份。

## 记账与发布

```bash
node tools/ledger-cloud.mjs pull
python3 ledger/scripts/ledger.py --root data/personal-investment apply --batch /path/to/batch.json
node tools/ledger-cloud.mjs push
python3 tools/dashboard.py deploy
```

push 校验并生成看板，以 ETag 条件写入并读回核对；冲突时保留批次，在新目录 pull 后重放，不允许强推旧账本。可用 `LEDGER_ROOT=/absolute/work-directory` 指定隔离目录。部署不会覆盖正式账本；每天更新数据不触发生产部署。

首次在新电脑部署，还需将忽略的 `data/personal-investment/netlify.json` 设置为包含目标 `site_id` 的 JSON。

## 保密与来源

仓库不包含账本、截图、Excel、密码、令牌或个人分析结果。正式账本保留来源指纹、结构化历史及更正关系，原始材料不是运行依赖。网站只公开加密看板，完整账本和历史版本仅通过授权访问 Blobs。详细口径见 `ledger/references/`。
