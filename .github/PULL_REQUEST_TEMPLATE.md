## 变更说明

<!-- 一段话说清这个 PR 做了什么；截图/GIF 对 GUI 改动很有帮助 -->

## 动机与关联

<!-- 关联 Issue（#编号）或简述背景；schema / 卡片字段 / 质检行为的变更请先开 Issue 讨论 -->

## 测试方式

<!-- 如何验证：单测名、--limit 2 小样本命令、手动走查步骤 -->

- [ ] `python -m pytest tests/ -v` 全绿
- [ ] 涉及生成流程的改动已用 `--limit 2` 小样本验证
- [ ] 新增/修改的行为有对应测试（mock，不发真实请求）

## 自查

- [ ] 未提交任何 API Key、`.env`、`data/` 个人数据（`grep -rn "sk-" --exclude-dir=data .` 已自查）
- [ ] 文档同步更新（README / CLAUDE.md / prompts、templates 的 README）
- [ ] 提示词/模板改动已核对占位符清单
