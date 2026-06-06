# Configuration Pipeline CLI

本地配置发布流水线 CLI，管理 dev/staging/prod 环境的应用配置、版本指针和审计事件。

## 功能特性

- **init**: 初始化流水线数据库
- **import**: 从 JSON 文件导入配置
- **validate**: 校验配置必填键和格式
- **plan**: 生成发布差异计划
- **apply**: 发布配置到指定环境
- **history**: 查看发布、回滚和审计历史
- **rollback**: 回滚到指定历史版本
- **export**: 导出审计数据（JSON/Markdown）
- **pending**: 标记版本待审批（prod 环境）
- **approve**: 审批待发布版本（release-manager 角色）
- **reject**: 拒绝待发布版本（release-manager 角色）
- **pending-list**: 查看待审批列表
- **lock**: 锁定环境，禁止发布/回滚（release-manager 角色）
- **unlock**: 解锁环境（release-manager 角色）
- **lock-status**: 查看环境锁定状态
- **preview**: 发布预演，在 apply 前查看变更、审批要求、锁定状态
- **preview show**: 查看历史预演结果（支持跨重启）
- **batch create**: 创建发布批次，串起多环境多版本发布
- **batch list**: 查看所有发布批次
- **batch show**: 查看批次详情和每一步状态
- **batch apply**: 按顺序执行批次步骤（支持跨重启继续）
- **batch export**: 导出批次为 JSON
- **batch import**: 导入批次到新库（支持 --force 覆盖冲突）

## 核心约束

- 必填键: `app_name`, `version`, `features`, `database`, `api_endpoints`
- 合法环境: `dev`, `staging`, `prod`
- 版本在同一环境中不能重复发布
- 发布到 prod 前必须先在 staging 发布成功
- **prod 环境需要审批后才能发布**
- **发布到 prod 需要 release-manager 角色**
- 失败时不会推进当前版本指针
- 所有操作记录保存在 SQLite 中

## 角色与权限

| 角色 | 权限 |
|------|------|
| `developer` | 基本操作（import, validate, plan, apply to dev/staging, pending, history, export） |
| `release-manager` | 所有 developer 权限 + approve, reject, lock, unlock, apply to prod |

### 设置角色方式

1. **CLI 参数**: `--role release-manager`
2. **环境变量**: `export PIPELINE_ROLE=release-manager` (Windows: `set PIPELINE_ROLE=release-manager`)

默认角色为 `developer`。

## 安装

```bash
pip install -r requirements.txt
```

## 快速开始

```bash
# 设置别名（可选）
alias pipeline="python pipeline.py"

# 查看帮助
python pipeline.py --help
```

---

## 🟢 成功路径命令

### 1. 初始化流水线
```bash
python pipeline.py init
```

### 2. 导入配置
```bash
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json
```

### 3. 校验配置
```bash
python pipeline.py validate 1.0.0
python pipeline.py validate 2.0.0 --env staging
```

### 4. 生成发布计划
```bash
# 发布 1.0.0 到 staging
python pipeline.py plan 1.0.0 staging

# 发布 1.0.0 到 prod（需要先过 staging）
python pipeline.py plan 1.0.0 prod

# 发布 2.0.0 到 staging
python pipeline.py plan 2.0.0 staging
```

### 5. 发布批次（Batch）
```bash
# 创建批次，按顺序发布 dev -> staging -> prod
python pipeline.py batch create release-v1 dev:1.0.0 staging:1.0.0 prod:1.0.0 --notes "v1 正式版本上线"

# 查看所有批次
python pipeline.py batch list

# 查看批次详情
python pipeline.py batch show release-v1

# 执行批次（遇到错误会停止，已成功步骤保留）
python pipeline.py batch apply release-v1 --yes

# 遇到 staging 锁定，解锁后重试失败步骤
python pipeline.py batch apply release-v1 --yes --retry

# 导出批次为 JSON
python pipeline.py batch export release-v1 -o release-v1-batch.json

# 导入批次到新库
python pipeline.py batch import release-v1-batch.json --role release-manager

# 强制覆盖导入冲突
python pipeline.py batch import release-v1-batch.json --role release-manager --force
```

### 6. 发布预演（Preview）
```bash
# 预演发布 2.0.0 到 dev
python pipeline.py preview run 2.0.0 dev

# 查看最近一次预演
python pipeline.py preview show 2.0.0 dev

# 查看所有预演记录
python pipeline.py preview show --all

# 基于预演发布（自动检查漂移）
python pipeline.py apply --from-preview 2.0.0 dev --yes

# 预演后如果状态变化（配置被修改、环境指针变化、锁定状态变化），会检测到漂移
# 使用 --ack-drift 确认漂移后继续（遵循权限规则）
python pipeline.py apply --from-preview 2.0.0 dev --yes --ack-drift
```

### 7. 审批与发布（Prod 环境）
```bash
# 先发布到 staging（必经过渡）
python pipeline.py apply 1.0.0 staging --yes

# 标记待审批（developer 角色）
python pipeline.py pending 1.0.0 prod --notes "新功能上线"

# 查看待审批列表
python pipeline.py pending-list

# 审批通过（release-manager 角色）
python pipeline.py approve 1.0.0 prod --role release-manager --notes "已验证，同意发布"

# 发布到 prod（需要 release-manager 角色）
python pipeline.py apply 1.0.0 prod --role release-manager --yes

# 发布 2.0.0 到 staging
python pipeline.py apply 2.0.0 staging --yes
```

### 8. 环境锁定与解锁
```bash
# 锁定 prod 环境（release-manager 角色）
python pipeline.py lock prod --role release-manager --reason "紧急维护中"

# 查看锁定状态
python pipeline.py lock-status

# 尝试发布到被锁定的环境（应该失败）
python pipeline.py apply 2.0.0 prod --yes

# 解锁环境
python pipeline.py unlock prod --role release-manager

# 解锁后回滚
python pipeline.py rollback prod 1.0.0 --reason "回滚到稳定版本" --yes
```

### 9. 查看历史
```bash
# 查看全部历史
python pipeline.py history

# 按环境筛选
python pipeline.py history --env staging

# 只看发布记录
python pipeline.py history --type releases
```

### 10. 回滚
```bash
# 回滚 staging 到 1.0.0
python pipeline.py rollback staging 1.0.0 --reason "feature rollback" --yes
```

### 11. 导出审计数据
```bash
# 导出为 JSON
python pipeline.py export --output audit.json --format json

# 导出为 Markdown
python pipeline.py export --output audit.md --format markdown

# 按环境导出
python pipeline.py export --env prod --output prod_audit.json

# 按状态筛选（success / failed）
python pipeline.py export --status failed --output failed_audit.json

# 按时间筛选（YYYY-MM-DD 或完整时间格式）
python pipeline.py export --since 2024-01-01 --output recent_audit.json
python pipeline.py export --since 2024-01-01T12:00:00 --output recent_audit.json

# 组合筛选（环境 + 状态）
python pipeline.py export --env prod --status failed --output prod_failed.json

# 组合所有筛选条件
python pipeline.py export --env staging --status success --since 2024-06-01 --format markdown --output filtered.md
```

---

## 🔴 失败路径命令（预期会失败）

### 1. 未初始化就操作
```bash
rm -f pipeline.db
python pipeline.py history
# 错误: Pipeline not initialized. Run 'pipeline init' first.
```

### 2. 导入缺键的配置
```bash
python pipeline.py import config_pipeline/examples/config_invalid.json
# 错误: Configuration is missing required keys: database, api_endpoints
```

### 3. 非法环境名
```bash
python pipeline.py validate 1.0.0 --env production
# 错误: Invalid environment 'production'. Must be one of: dev, staging, prod
```

### 4. 重复版本
```bash
# 先发布一次
python pipeline.py apply 1.0.0 staging --yes

# 再发布同版本
python pipeline.py apply 1.0.0 staging --yes
# 错误: Version 1.0.0 already exists in staging environment
```

### 5. 未经过 staging 就发布 prod
```bash
# 确保 2.0.0 没有在 staging 发布过
python pipeline.py plan 2.0.0 prod
# 错误: Version 2.0.0 must be deployed to staging before prod
```

### 6. 回滚不存在版本
```bash
python pipeline.py rollback staging 99.99.99 --yes
# 错误: Version 99.99.99 not found in staging environment
```

### 7. 无权限审批（developer 尝试审批）
```bash
# 先创建待审批
python pipeline.py apply 2.0.0 staging --yes
python pipeline.py pending 2.0.0 prod --notes "待审批"

# 用 developer 角色审批（默认角色，失败）
python pipeline.py approve 2.0.0 prod --role developer
# 错误: Permission denied for 'approve'. Required role: release-manager. Your role: developer
```

### 8. 锁定环境发布冲突
```bash
# 锁定环境
python pipeline.py lock prod --role release-manager --reason "发布冻结"

# 先确保有审批过的版本
python pipeline.py unlock prod --role release-manager
python pipeline.py apply 2.0.0 staging --yes
python pipeline.py pending 2.0.0 prod
python pipeline.py approve 2.0.0 prod --role release-manager
python pipeline.py lock prod --role release-manager --reason "发布冻结"

# 尝试发布（失败）
python pipeline.py apply 2.0.0 prod --yes
# 错误: Environment 'prod' is locked. Reason: 发布冻结
```

### 9. 未审批就发布 prod
```bash
# 先解锁
python pipeline.py unlock prod --role release-manager

# 仅 pending 不 approve，尝试发布
python pipeline.py apply 2.0.0 prod --yes
# 错误: Version 2.0.0 requires approval before releasing to prod. Use 'pipeline approve' first.
```

### 10. 无效角色
```bash
python pipeline.py approve 2.0.0 prod --role admin
# 错误: Invalid role 'admin'. Must be one of: developer, release-manager
```

### 11. 无效 export --status 值
```bash
python pipeline.py export --status invalid
# 错误: Invalid status 'invalid'. Must be one of: success, failed
```

### 12. 无效 export --since 格式
```bash
python pipeline.py export --since not-a-date
# 错误: Invalid --since format 'not-a-date'. Expected YYYY-MM-DD or ISO datetime (e.g., 2024-01-01 or 2024-01-01T12:00:00)
```

### 13. 预演后配置内容漂移
```bash
# 先创建预演
python pipeline.py preview run 2.0.0 dev

# 直接修改 SQLite 中同版本配置内容（模拟配置被篡改）
# (通过 UPDATE configs SET config_json = '...' WHERE version = '2.0.0'

# 尝试基于预演发布（预期失败 - 配置内容已漂移）
python pipeline.py apply --from-preview 2.0.0 dev --yes
# 错误: Preview drift detected. State has changed since preview:
#   - Target config '2.0.0' content changed: app_name: myapp -> myapp_modified, ...
```

### 14. developer 不能绕过 prod 配置漂移
```bash
# 先发布到 staging
python pipeline.py apply 2.0.0 staging --yes

# 创建 prod 预演
python pipeline.py preview run 2.0.0 prod --role release-manager

# 修改配置内容
# UPDATE configs SET config_json = '...' WHERE version = '2.0.0'

# developer 尝试确认漂移（预期失败）
python pipeline.py apply --from-preview 2.0.0 prod --yes --ack-drift --role developer
# 错误: Cannot acknowledge drift: developer cannot acknowledge drift in prod environment
```

---

## ✅ 完整验证链路

### 链路 0: 批次最短验证路径（5 步搞定）
```bash
# 1. 初始化
rm -f pipeline.db
python pipeline.py init

# 2. 导入配置
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json

# 3. 创建批次（dev -> staging 发布 1.0.0）
python pipeline.py batch create quick-batch dev:1.0.0 staging:1.0.0 --notes "快速验证"

# 4. 查看批次详情
python pipeline.py batch show quick-batch

# 5. 执行批次
python pipeline.py batch apply quick-batch --yes

# 验证：两步都应为 SUCCESS
python pipeline.py batch show quick-batch
```

**进阶：跨重启继续执行**
```bash
# 第一步：创建带失败的批次
python pipeline.py batch create resume-batch dev:1.0.0 staging:2.0.0
python pipeline.py lock staging --role release-manager --reason "测试"
python pipeline.py batch apply resume-batch --yes  # 第一步成功，第二步失败

# 关闭终端，重新打开
# 第二步：解锁并重试
python pipeline.py unlock staging --role release-manager
python pipeline.py batch apply resume-batch --yes --retry

# 验证：两步都成功
python pipeline.py batch show resume-batch
```

**进阶：导入导出**
```bash
# 导出批次
python pipeline.py batch export quick-batch -o quick-batch.json

# 新环境导入
rm -f pipeline.db
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py batch import quick-batch.json --role release-manager
```

### 链路 1: 成功审批发布（完整流程）
```bash
# 清理环境
rm -f pipeline.db
python pipeline.py init

# 导入配置
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json

# 发布到 staging
python pipeline.py apply 1.0.0 staging --yes

# 标记待审批
python pipeline.py pending 1.0.0 prod --notes "新功能上线"

# 查看待审批列表
python pipeline.py pending-list

# 审批通过
python pipeline.py approve 1.0.0 prod --role release-manager --notes "已验证"

# 发布到 prod（需要 release-manager 角色）
python pipeline.py apply 1.0.0 prod --role release-manager --yes

# 验证结果：prod 环境当前版本应为 1.0.0
python pipeline.py history --type releases
```

### 链路 2: 无权限审批（权限控制）
```bash
# 发布到 staging
python pipeline.py apply 2.0.0 staging --yes

# 标记待审批
python pipeline.py pending 2.0.0 prod

# 用 developer 角色审批（失败）
python pipeline.py approve 2.0.0 prod --role developer
# 预期错误：Permission denied for 'approve'. Required role: release-manager.

# 用环境变量设置角色后审批（成功）
$env:PIPELINE_ROLE="release-manager"
python pipeline.py approve 2.0.0 prod
```

### 链路 3: 锁定环境发布冲突
```bash
# 锁定 prod 环境
python pipeline.py lock prod --role release-manager --reason "紧急发布冻结"

# 查看锁定状态
python pipeline.py lock-status

# 尝试发布（失败）
python pipeline.py apply 2.0.0 prod --yes
# 预期错误：Environment 'prod' is locked. Reason: 紧急发布冻结

# 查看错误日志
python pipeline.py history --type audit
```

### 链路 4: 解锁后回滚
```bash
# 先确保 prod 有版本
python pipeline.py unlock prod --role release-manager
python pipeline.py apply 2.0.0 prod --role release-manager --yes

# 锁定后尝试回滚（失败）
python pipeline.py lock prod --role release-manager --reason "维护中"
python pipeline.py rollback prod 1.0.0 --yes
# 预期错误：Environment 'prod' is locked.

# 解锁后回滚（成功）
python pipeline.py unlock prod --role release-manager
python pipeline.py rollback prod 1.0.0 --reason "回滚到稳定版" --yes

# 验证：prod 已回滚到 1.0.0
python pipeline.py history --type rollbacks
```

### 链路 5: 数据持久化验证
```bash
# 执行一些操作
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py pending 1.0.0 prod
python pipeline.py lock prod --role release-manager --reason "测试锁定"

# 关闭终端，重新打开
cd /path/to/project

# 验证历史和状态仍然存在
python pipeline.py history
python pipeline.py pending-list
python pipeline.py lock-status

# 导出审计数据（包含审批人、锁定原因、冲突原因）
python pipeline.py export --output audit.json --format json
python pipeline.py export --output audit.md --format markdown
```

### 链路 6: prod 发布权限验证（修复权限绕过）
```bash
# 初始化环境
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py pending 1.0.0 prod
python pipeline.py approve 1.0.0 prod --role release-manager

# 测试 1: 未传角色（默认 developer）发布 prod - 预期失败
python pipeline.py apply 1.0.0 prod --yes
# 预期错误：Permission denied for 'apply'. Required role: release-manager. Your role: developer

# 测试 2: 显式传 developer 角色发布 prod - 预期失败
python pipeline.py apply 1.0.0 prod --role developer --yes
# 预期错误：Permission denied for 'apply'. Required role: release-manager. Your role: developer

# 测试 3: 环境变量 PIPELINE_ROLE=developer 发布 prod - 预期失败
$env:PIPELINE_ROLE="developer"
python pipeline.py apply 1.0.0 prod --yes
# 预期错误：Permission denied for 'apply'. Required role: release-manager. Your role: developer

# 测试 4: release-manager 角色发布 prod - 预期成功
$env:PIPELINE_ROLE="release-manager"
python pipeline.py apply 1.0.0 prod --yes
# 预期成功

# 清除环境变量
Remove-Item Env:PIPELINE_ROLE -ErrorAction SilentlyContinue
```

### 链路 7: 回归验证（其他功能不受影响）
```bash
# 初始化环境
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json

# 测试 1: staging apply 不需要 release-manager - 预期成功（默认 developer）
python pipeline.py apply 1.0.0 staging --yes

# 测试 2: 锁定冲突正常工作
python pipeline.py lock prod --role release-manager --reason "测试锁定"
python pipeline.py apply 1.0.0 staging --yes  # staging 不受 prod 锁定影响
# 预期成功

# 测试 3: pending-list 正常工作
python pipeline.py pending 2.0.0 prod
python pipeline.py pending-list
# 预期显示 pending 状态

# 测试 4: history 正常工作
python pipeline.py history
# 预期显示完整历史

# 测试 5: export 正常工作
python pipeline.py export --output regression_test.json --format json
# 预期导出包含所有字段
```

### 链路 8: 配置内容漂移检测与处理（目标配置漂移修复验证）
```bash
# 初始化环境
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json

# 先发布一个基准版本
python pipeline.py apply 1.0.0 dev --yes

# 创建预演
python pipeline.py preview run 2.0.0 dev
# 预期：显示 15 个变更，预演已保存

# 查看预演结果
python pipeline.py preview show 2.0.0 dev
# 预期：显示预演详情，包括 SNAPSHOT STATE 和 CHANGES SUMMARY

# 直接修改 SQLite 中同版本配置（模拟配置被篡改）
# 可以使用如下 Python 脚本：
#   import sqlite3, json
#   conn = sqlite3.connect('pipeline.db')
#   cursor = conn.cursor()
#   cursor.execute("SELECT config_json FROM configs WHERE version = '2.0.0'")
#   config = json.loads(cursor.fetchone()[0])
#   config['database']['pool_size'] = 999
#   config['app_name'] = 'myapp_drifted'
#   cursor.execute("UPDATE configs SET config_json = ? WHERE version = '2.0.0'",
#                  (json.dumps(config),))
#   conn.commit()
#   conn.close()

# 测试 1: 尝试基于预演发布（不确认漂移）- 预期失败
python pipeline.py apply --from-preview 2.0.0 dev --yes
# 预期错误：Preview drift detected. State has changed since preview:
#   - Target config '2.0.0' content changed: app_name: myapp -> myapp_drifted, database.pool_size: 20 -> 999

# 验证：环境指针和 release 内容未变
python pipeline.py history --type releases
# 预期：只有 1.0.0 -> dev，没有 2.0.0 的记录

# 验证：audit_logs 记录了 drift_detected
python pipeline.py history --type audit | Select-String -Pattern "drift_detected"
# 预期：存在 drift_detected 记录

# 测试 2: 使用 --ack-drift 确认漂移后发布 - 预期成功（非 prod，非锁定变更）
python pipeline.py apply --from-preview 2.0.0 dev --yes --ack-drift
# 预期：! DRIFT DETECTED but acknowledged:
#   ! Target config '2.0.0' content changed: ...
#   Proceeding with apply (acknowledged by developer)
#   SUCCESS: Version 2.0.0 applied to dev

# 验证：漂移后的配置已正确发布
python pipeline.py export --output drift_verify.json --format json
# 预期：release 中的配置包含 pool_size=999 和 app_name=myapp_drifted
```

---

## 🔄 重启验证命令

验证数据持久化，重新运行命令后 history 保持一致：

```bash
# 第一步：执行一些操作
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py pending 1.0.0 prod
python pipeline.py lock prod --role release-manager --reason "测试"

# 第二步：完全退出并重试
# （模拟重启 - 不需要做任何特殊操作，SQLite 已持久化）

# 第三步：验证历史记录一致
python pipeline.py history > history_before.txt

# 关闭终端，重新打开
cd /path/to/project

python pipeline.py history > history_after.txt
diff history_before.txt history_after.txt
# 应该没有差异
```

或者更简单的验证：
```bash
# 初始操作
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py pending 1.0.0 prod
python pipeline.py approve 1.0.0 prod --role release-manager
python pipeline.py apply 1.0.0 prod --role release-manager --yes
python pipeline.py history --type releases

# 关闭终端，重新打开
cd /path/to/project

# 验证历史仍然存在
python pipeline.py history --type releases
python pipeline.py pending-list --all
python pipeline.py lock-status
```

---

## 项目结构

```
.
├── pipeline.py                 # CLI 入口
├── requirements.txt            # 依赖
├── README.md                   # 本文档
└── config_pipeline/
    ├── __init__.py
    ├── __main__.py
    ├── cli.py                  # 主 CLI 入口
    ├── commands/
    │   ├── __init__.py
    │   ├── init_cmd.py         # init 命令
    │   ├── import_cmd.py       # import 命令
    │   ├── validate_cmd.py     # validate 命令
    │   ├── plan_cmd.py         # plan 命令
    │   ├── apply_cmd.py        # apply 命令
    │   ├── history_cmd.py      # history 命令
    │   ├── rollback_cmd.py     # rollback 命令
    │   ├── export_cmd.py       # export 命令
    │   ├── lock_cmd.py         # lock/unlock/lock-status 命令
    │   ├── approve_cmd.py      # approve/reject 命令
    │   ├── pending_cmd.py      # pending/pending-list 命令
    │   ├── preview_cmd.py      # preview/preview show 命令（发布预演和漂移检查）
    │   └── batch_cmd.py        # batch 命令（发布批次：create/list/show/apply/export/import）
    ├── utils/
    │   ├── __init__.py
    │   ├── errors.py           # 错误定义
    │   ├── database.py         # SQLite 数据层
    │   └── diff.py             # 差异计算
    └── examples/
        ├── config_v1.json      # 示例配置 v1
        ├── config_v2.json      # 示例配置 v2
        ├── config_v3.json      # 示例配置 v3
        └── config_invalid.json # 无效配置（用于测试）
```

## 数据库结构

- `environments`: 环境信息和当前版本指针
- `configs`: 导入的配置快照
- `releases`: 发布记录（成功/失败，含审批人、冲突原因）
- `rollbacks`: 回滚记录
- `audit_logs`: 所有操作的审计日志
- `error_logs`: 错误详情记录
- `environment_locks`: 环境锁定状态（锁定原因、锁定人、冲突原因）
- `approvals`: 审批记录（待审批/已审批/已拒绝，审批人、冲突原因）
- `previews`: 发布预演记录（包含目标配置快照、环境指针快照、锁状态快照、审批状态快照、变更摘要）
- `batches`: 发布批次（名称、描述、状态、操作者、备注）
- `batch_steps`: 批次步骤（环境、版本、状态、错误原因、release_id）

## 审计导出内容

导出的审计文件包含：
- **计划摘要**: 每次发布的变更统计（新增/删除/修改数量）
- **操作者**: 当前系统用户名
- **时间戳**: 操作执行时间
- **错误原因**: 失败操作的具体错误信息
- **环境状态**: 各环境当前版本
- **审批人**: prod 环境发布的审批人
- **锁定原因**: 环境锁定的原因说明
- **冲突失败原因**: 发布/回滚冲突的具体原因
