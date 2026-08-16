# 当前任务

状态只使用：`planned`、`active`、`review`、`blocked`、`done`、`cancelled`。

| ID | 任务 | 状态 | Owner | 依赖 | Handoff |
|---|---|---|---|---|---|
| T-001 | 整理当前 v0.4.0 工作区，建立项目接力合同并推送开发分支 | done | primary Codex integrator | 无 | [handoffs/T-001.md](handoffs/T-001.md) |
| T-002 | 审查开发分支并决定是否合并 main、创建 v0.4.0 Release | planned | user | T-001 | 待创建；未授权执行 |

## 执行纪律

- 同一时刻最多一个 `active` 写任务。
- `planned` 不等于已授权执行。
- 合并 `main`、Release 和对外宣发必须在独立 handoff 中明确授权。
