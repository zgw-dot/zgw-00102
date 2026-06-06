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
- **window create**: 创建发布窗口（关闭指定时间段的发布）
- **window list**: 查看所有发布窗口
- **window status**: 查看环境窗口当前状态
- **window disable**: 禁用（重新打开）发布窗口
- **package create**: 创建变更包（将多个配置版本打包，可复查）
- **package show**: 查看变更包详情
- **package list**: 列出所有变更包
- **package verify**: 校验变更包完整性和配置哈希
- **package sign**: 签收变更包（release-manager 角色，prod 包）
- **package revoke**: 撤销变更包签收（release-manager 角色）
- **package export**: 导出变更包为 JSON 文件
- **package import**: 从 JSON 文件导入变更包

## 核心约束

- 必填键: `app_name`, `version`, `features`, `database`, `api_endpoints`
- 合法环境: `dev`, `staging`, `prod`
- 版本在同一环境中不能重复发布
- 发布到 prod 前必须先在 staging 发布成功
- **prod 环境需要审批后才能发布**
- **发布到 prod 需要 release-manager 角色**
- 失败时不会推进当前版本指针
- 所有操作记录保存在 SQLite 中
- **发布窗口关闭时，apply/batch apply/rollback 必须失败，不推进版本、不写 success release**
- **只有 release-manager 可以管理 prod 环境的发布窗口**
- **只有 release-manager 可以使用 --override-window 强行执行操作**
- **变更包名不能重复，缺失版本会失败，导入时摘要哈希不一致会失败**
- **所有变更包操作都会写入 audit_logs 和 error_logs**
- **prod 环境的变更包只有 release-manager 能签收或撤销签收**
- **developer 只能创建非 prod 环境的变更包和查看所有包**
- **apply 和 batch apply 发布到 prod 时，对应版本必须在已签收的变更包中**
- **发布到 prod 失败时，不会推进 current_version 或写入 success release 记录**

## 角色与权限

| 角色 | 权限 |
|------|------|
| `developer` | 基本操作（import, validate, plan, apply to dev/staging, pending, history, export）+ 管理 dev/staging 发布窗口 + 查看所有窗口 + 创建非 prod 变更包 + 查看所有变更包 + 校验变更包完整性 |
| `release-manager` | 所有 developer 权限 + approve, reject, lock, unlock, apply to prod + 管理所有环境发布窗口 + --override-window + 签收/撤销签收 prod 变更包 + 创建 prod 变更包 |

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

### 5. 发布预演（Preview）
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

### 6. 审批与发布（Prod 环境）
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

### 7. 环境锁定与解锁
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

### 8. 查看历史
```bash
# 查看全部历史
python pipeline.py history

# 按环境筛选
python pipeline.py history --env staging

# 只看发布记录
python pipeline.py history --type releases
```

### 9. 回滚
```bash
# 回滚 staging 到 1.0.0
python pipeline.py rollback staging 1.0.0 --reason "feature rollback" --yes
```

### 10. 发布窗口管理
```bash
# 创建发布窗口（关闭 dev 环境 24 小时）
python pipeline.py window create dev 2024-01-01T00:00:00 2024-01-02T00:00:00 --reason "系统维护"

# 创建 prod 环境发布窗口（需要 release-manager 角色）
python pipeline.py window create prod 2024-12-24T18:00:00 2024-12-26T09:00:00 --reason "假期发布冻结" --role release-manager

# 查看所有发布窗口
python pipeline.py window list

# 查看指定环境的发布窗口
python pipeline.py window list --env prod

# 查看所有窗口（包括已禁用的）
python pipeline.py window list --all

# 查看环境当前窗口状态
python pipeline.py window status
python pipeline.py window status prod

# 禁用（重新打开）发布窗口
python pipeline.py window disable 1
python pipeline.py window disable 2 --role release-manager
```

### 11. 发布窗口期间的操作与覆盖
```bash
# 窗口关闭时发布（应该失败）
python pipeline.py apply 2.0.0 dev --yes
# 错误: Environment 'dev' is in a closed release window. Reason: 系统维护.

# 使用 --override-window 强行发布（需要 release-manager 角色 + 原因）
python pipeline.py apply 2.0.0 dev --yes --override-window --override-reason "紧急修复线上 Bug" --role release-manager

# batch apply 期间遇到窗口关闭（整个 batch 失败）
python pipeline.py batch apply dev:2.0.0 staging:2.0.0 --yes

# batch apply 强行覆盖窗口
python pipeline.py batch apply dev:2.0.0 staging:2.0.0 --yes --override-window --override-reason "紧急发布" --role release-manager

# rollback 期间遇到窗口关闭
python pipeline.py rollback dev 1.0.0 --reason "回滚" --yes

# rollback 强行覆盖窗口
python pipeline.py rollback dev 1.0.0 --reason "紧急回滚" --yes --override-window --override-reason "紧急回滚修复" --role release-manager
```

### 12. 变更包管理
```bash
# developer 创建非 prod 变更包
python pipeline.py package create staging-release-001 staging 1.0.0 2.0.0

# release-manager 创建 prod 变更包
python pipeline.py package create prod-release-001 prod 1.0.0 2.0.0 --role release-manager

# 查看变更包详情
python pipeline.py package show prod-release-001

# 列出所有变更包
python pipeline.py package list

# 按环境筛选
python pipeline.py package list --env prod

# 校验变更包完整性
python pipeline.py package verify prod-release-001

# release-manager 签收变更包
python pipeline.py package sign prod-release-001 --role release-manager --notes "已验证所有配置"

# 撤销签收
python pipeline.py package revoke prod-release-001 --role release-manager --reason "发现问题，需要重新验证"

# 导出变更包为 JSON
python pipeline.py package export prod-release-001 --output prod-release-001.json

# 从 JSON 导入变更包（需要对应版本已存在）
python pipeline.py package import prod-release-001.json --role release-manager
```

### 13. 使用变更包的完整 Prod 发布流程
```bash
# 1. 导入配置
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json

# 2. 发布到 staging
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py apply 2.0.0 staging --yes

# 3. developer 创建 prod 变更包（会失败，需要 release-manager）
python pipeline.py package create prod-release-001 prod 1.0.0 2.0.0
# 错误: Permission denied for 'create_package'. Required role: release-manager.

# 4. release-manager 创建 prod 变更包
python pipeline.py package create prod-release-001 prod 1.0.0 2.0.0 --role release-manager

# 5. 校验变更包完整性
python pipeline.py package verify prod-release-001

# 6. release-manager 签收变更包
python pipeline.py package sign prod-release-001 --role release-manager --notes "验证通过"

# 7. 审批版本
python pipeline.py pending 1.0.0 prod
python pipeline.py pending 2.0.0 prod
python pipeline.py approve 1.0.0 prod --role release-manager
python pipeline.py approve 2.0.0 prod --role release-manager

# 8. 发布到 prod（会自动检查版本是否在已签收的包中）
python pipeline.py apply 1.0.0 prod --role release-manager --yes
python pipeline.py apply 2.0.0 prod --role release-manager --yes

# 9. 或使用 batch apply
python pipeline.py batch apply prod:1.0.0 prod:2.0.0 --role release-manager --yes
```

### 14. 导出审计数据
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

# 导出审计查看窗口覆盖记录
python pipeline.py export --output window_audit.json --format json
# 审计日志中包含 window_blocked, window_overridden, window_override_denied 等事件
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

### 15. 发布窗口关闭时发布失败
```bash
# 创建发布窗口
python pipeline.py window create dev 2024-01-01T00:00:00 2024-12-31T23:59:59 --reason "发布冻结"

# 尝试发布（失败）
python pipeline.py apply 2.0.0 dev --yes
# 错误: Environment 'dev' is in a closed release window. Reason: 发布冻结.
```

### 16. 非法时间格式创建窗口失败
```bash
python pipeline.py window create dev invalid-time 2024-12-31T23:59:59 --reason "测试"
# 错误: Invalid datetime format: invalid-time. Expected ISO format (e.g., 2024-01-01T12:00:00)
```

### 17. 结束时间早于开始时间失败
```bash
python pipeline.py window create dev 2024-12-31T23:59:59 2024-01-01T00:00:00 --reason "测试"
# 错误: End time (2024-01-01T00:00:00) must be after start time (2024-12-31T23:59:59)
```

### 18. 重叠窗口创建失败
```bash
# 创建第一个窗口
python pipeline.py window create dev 2024-01-01T00:00:00 2024-01-07T23:59:59 --reason "第一周"

# 创建重叠窗口（失败）
python pipeline.py window create dev 2024-01-03T00:00:00 2024-01-10T23:59:59 --reason "重叠"
# 错误: Overlapping release window detected for environment 'dev':
#   - ID: 1, 2024-01-01T00:00:00 to 2024-01-07T23:59:59
```

### 19. 未知环境创建窗口失败
```bash
python pipeline.py window create invalid-env 2024-01-01T00:00:00 2024-12-31T23:59:59 --reason "测试"
# 错误: Invalid environment 'invalid-env'. Must be one of: dev, staging, prod
```

### 20. developer 管理 prod 窗口失败
```bash
python pipeline.py window create prod 2024-01-01T00:00:00 2024-12-31T23:59:59 --reason "测试" --role developer
# 错误: Permission denied for 'create_release_window'. Required role: release-manager. Your role: developer
```

### 21. developer 覆盖窗口失败
```bash
# 创建窗口
python pipeline.py window create dev 2024-01-01T00:00:00 2024-12-31T23:59:59 --reason "测试"

# developer 尝试覆盖（失败）
python pipeline.py apply 2.0.0 dev --yes --override-window --override-reason "紧急" --role developer
# 错误: Permission denied to override release window for 'apply'. Required role: release-manager. Your role: developer
```

### 22. 覆盖窗口缺少原因失败
```bash
# release-manager 尝试覆盖但不提供原因（失败）
python pipeline.py apply 2.0.0 dev --yes --override-window --role release-manager
# 错误: Override reason is required when using --override-window
```

### 23. 创建变更包使用缺失版本
```bash
# 使用不存在的版本创建变更包
python pipeline.py package create test-pkg staging 99.0.0
# 错误: Version 99.0.0 not found
```

### 24. 创建重名变更包
```bash
# 创建第一个包
python pipeline.py package create test-pkg staging 1.0.0

# 再次创建同名包（失败）
python pipeline.py package create test-pkg staging 2.0.0
# 错误: Change package 'test-pkg' already exists
```

### 25. developer 创建 prod 变更包
```bash
python pipeline.py package create prod-pkg prod 1.0.0 --role developer
# 错误: Permission denied for 'create_package'. Required role: release-manager. Your role: developer
```

### 26. developer 签收变更包
```bash
# 先创建 prod 包
python pipeline.py package create prod-pkg prod 1.0.0 --role release-manager

# developer 尝试签收（失败）
python pipeline.py package sign prod-pkg --role developer
# 错误: Permission denied for 'sign_package'. Required role: release-manager. Your role: developer
```

### 27. 签收已签收的变更包
```bash
# 先签收
python pipeline.py package sign prod-pkg --role release-manager

# 再次签收（失败）
python pipeline.py package sign prod-pkg --role release-manager
# 错误: Package 'prod-pkg' is already signed
```

### 28. developer 撤销签收
```bash
# developer 尝试撤销（失败）
python pipeline.py package revoke prod-pkg --role developer
# 错误: Permission denied for 'revoke_package'. Required role: release-manager. Your role: developer
```

### 29. 撤销未签收的变更包
```bash
# 创建包但不签收
python pipeline.py package create unsigned-pkg staging 1.0.0

# 尝试撤销（失败）
python pipeline.py package revoke unsigned-pkg --role release-manager
# 错误: Package 'unsigned-pkg' is not signed, cannot revoke
```

### 30. 导入变更包摘要哈希不匹配
```bash
# 先导出一个包
python pipeline.py package export test-pkg --output test-pkg.json

# 手动修改导出文件中的 summary_hash

# 导入被篡改的包（失败）
python pipeline.py package import test-pkg.json
# 错误: Package 'test-pkg' summary mismatch during import.
```

### 31. 导入变更包缺失版本
```bash
# 在一个新数据库中导入包（对应版本不存在）
# 错误: Version 1.0.0 not found in database, required by package
```

### 32. 发布到 prod 但版本不在已签收包中
```bash
# 版本已审批但不在已签收的变更包中
python pipeline.py apply 1.0.0 prod --role release-manager --yes
# 错误: Version '1.0.0' must be in a signed package before release to prod.
```

### 33. 撤销签收后发布失败
```bash
# 先签收再撤销
python pipeline.py package revoke prod-pkg --role release-manager --reason "发现问题"

# 尝试发布（失败）
python pipeline.py apply 1.0.0 prod --role release-manager --yes
# 错误: Version '1.0.0' must be in a signed package before release to prod.
```

### 34. batch apply 到 prod 但版本不在已签收包中
```bash
python pipeline.py batch apply prod:1.0.0 --role release-manager --yes
# 错误: Version '1.0.0' must be in a signed package before release to prod.
```

---

## ✅ 完整验证链路

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

### 链路 9: 发布窗口管控完整流程
```bash
# 初始化环境
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json

# 测试 1: 创建 dev 环境发布窗口（developer 角色）- 成功
python pipeline.py window create dev 2024-01-01T00:00:00 2024-12-31T23:59:59 --reason "发布冻结期" --role developer
# 预期：RELEASE WINDOW CREATED，ID: 1

# 测试 2: 创建重叠窗口 - 失败
python pipeline.py window create dev 2024-06-01T00:00:00 2024-07-01T23:59:59 --reason "重叠测试" --role developer
# 预期错误：Overlapping release window detected

# 测试 3: 查看窗口列表
python pipeline.py window list
# 预期显示 ID: 1, ENV: dev, STATUS: CURRENT, Reason: 发布冻结期

# 测试 4: 查看窗口状态
python pipeline.py window status dev
# 预期：dev CLOSED

# 测试 5: 窗口关闭期间 apply - 失败，不推进版本
python pipeline.py apply 1.0.0 dev --yes
# 预期错误：Environment 'dev' is in a closed release window

# 验证：环境版本未变
python pipeline.py history --type releases
# 预期：没有 1.0.0 -> dev 的 success 记录

# 验证：audit_logs 记录了 window_blocked
python pipeline.py export --output audit1.json --format json
# 预期：存在 action=window_blocked 的审计记录

# 测试 6: developer 尝试覆盖窗口 - 失败
python pipeline.py apply 1.0.0 dev --yes --override-window --override-reason "紧急修复" --role developer
# 预期错误：Permission denied to override release window for 'apply'

# 测试 7: release-manager 覆盖窗口但缺少原因 - 失败
python pipeline.py apply 1.0.0 dev --yes --override-window --role release-manager
# 预期错误：Override reason is required when using --override-window

# 测试 8: release-manager 覆盖窗口 - 成功
python pipeline.py apply 1.0.0 dev --yes --override-window --override-reason "紧急修复线上 Bug" --role release-manager
# 预期：! Release window overridden: 紧急修复线上 Bug
#       SUCCESS: Version 1.0.0 applied to dev

# 验证：覆盖原因已记录
python pipeline.py export --output audit2.json --format json
# 预期：release 记录包含 window_override_reason，audit 包含 window_overridden 事件

# 测试 9: 禁用窗口 - 成功
python pipeline.py window disable 1 --role developer
# 预期：RELEASE WINDOW DISABLED

# 测试 10: 窗口禁用后发布 - 成功
python pipeline.py apply 2.0.0 dev --yes
# 预期：SUCCESS: Version 2.0.0 applied to dev

# 验证：dev 环境版本为 2.0.0
python pipeline.py history --type releases
# 预期：1.0.0 -> dev (success), 2.0.0 -> dev (success)
```

### 链路 10: prod 环境窗口权限控制
```bash
# 初始化环境
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py pending 1.0.0 prod
python pipeline.py approve 1.0.0 prod --role release-manager

# 测试 1: developer 创建 prod 窗口 - 失败
python pipeline.py window create prod 2024-01-01T00:00:00 2024-12-31T23:59:59 --reason "生产冻结" --role developer
# 预期错误：Permission denied for 'create_release_window'. Required role: release-manager.

# 测试 2: release-manager 创建 prod 窗口 - 成功
python pipeline.py window create prod 2024-01-01T00:00:00 2024-12-31T23:59:59 --reason "生产冻结" --role release-manager
# 预期：RELEASE WINDOW CREATED

# 测试 3: 窗口期间发布 prod - 失败
python pipeline.py apply 1.0.0 prod --yes --role release-manager
# 预期错误：Environment 'prod' is in a closed release window

# 测试 4: developer 禁用 prod 窗口 - 失败
python pipeline.py window disable 1 --role developer
# 预期错误：Permission denied for 'disable_release_window'. Required role: release-manager.

# 测试 5: release-manager 禁用 prod 窗口 - 成功
python pipeline.py window disable 1 --role release-manager
# 预期：RELEASE WINDOW DISABLED

# 测试 6: 窗口禁用后发布 prod - 成功
python pipeline.py apply 1.0.0 prod --yes --role release-manager
# 预期：SUCCESS: Version 1.0.0 applied to prod
```

### 链路 11: batch apply 和 rollback 窗口控制
```bash
# 初始化环境
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json

# 先发布 1.0.0 到 dev
python pipeline.py apply 1.0.0 dev --yes

# 创建 dev 窗口
python pipeline.py window create dev 2024-01-01T00:00:00 2024-12-31T23:59:59 --reason "冻结"

# 测试 1: batch apply 遇到窗口 - 失败
python pipeline.py batch apply dev:2.0.0 staging:2.0.0 --yes
# 预期：Step 1 FAILED，Step 2 SKIP，整个 batch 失败

# 验证：版本未推进
python pipeline.py history --type releases
# 预期：只有 1.0.0 -> dev

# 测试 2: batch apply 覆盖窗口 - 成功
python pipeline.py batch apply dev:2.0.0 staging:2.0.0 --yes --override-window --override-reason "紧急批量发布" --role release-manager
# 预期：两步都成功，显示 Window overridden

# 测试 3: rollback 遇到窗口 - 失败
python pipeline.py rollback dev 1.0.0 --reason "回滚" --yes
# 预期错误：Environment 'dev' is in a closed release window

# 测试 4: rollback 覆盖窗口 - 成功
python pipeline.py rollback dev 1.0.0 --reason "紧急回滚" --yes --override-window --override-reason "紧急回滚修复" --role release-manager
# 预期：! Release window overridden: 紧急回滚修复
#       SUCCESS: Rollback to 1.0.0 completed in dev
```

### 链路 12: 跨重启持久化验证
```bash
# 初始化环境并创建窗口
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py window create dev 2024-01-01T00:00:00 2024-12-31T23:59:59 --reason "持久化测试" --role developer
python pipeline.py window create staging 2024-06-01T00:00:00 2024-07-01T23:59:59 --reason "阶段性冻结" --role developer

# 验证窗口存在
python pipeline.py window list > windows_before.txt

# 模拟重启（不需要特殊操作，SQLite 已持久化）
# 关闭终端，重新打开，cd 到项目目录

# 验证窗口仍然存在
python pipeline.py window list > windows_after.txt
# windows_before.txt 和 windows_after.txt 内容应该一致

# 验证窗口状态仍然生效
python pipeline.py apply 1.0.0 dev --yes
# 预期仍然失败：Environment 'dev' is in a closed release window

# 验证审计记录完整
python pipeline.py export --output persistence_audit.json --format json
# 预期包含所有 window 相关的操作记录
```

### 链路 13: 变更包完整发布流程
```bash
# 初始化环境
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json

# 测试 1: developer 创建 prod 变更包 - 失败
python pipeline.py package create prod-release-001 prod 1.0.0 2.0.0 --role developer
# 预期错误：Permission denied for 'create_package'. Required role: release-manager.

# 测试 2: release-manager 创建 prod 变更包 - 成功
python pipeline.py package create prod-release-001 prod 1.0.0 2.0.0 --role release-manager
# 预期：Package 'prod-release-001' created successfully

# 测试 3: 查看变更包详情
python pipeline.py package show prod-release-001
# 预期显示包名、目标环境、版本列表、配置摘要、签收状态等

# 测试 4: 校验变更包完整性
python pipeline.py package verify prod-release-001
# 预期：Package 'prod-release-001' is valid. All version hashes match.

# 测试 5: developer 签收变更包 - 失败
python pipeline.py package sign prod-release-001 --role developer
# 预期错误：Permission denied for 'sign_package'. Required role: release-manager.

# 测试 6: release-manager 签收变更包 - 成功
python pipeline.py package sign prod-release-001 --role release-manager --notes "验证通过"
# 预期：Package 'prod-release-001' signed successfully

# 测试 7: 发布到 prod 无签收包（使用不在包中的版本）- 先导入一个新版本
python pipeline.py import config_pipeline/examples/config_v3.json
python pipeline.py apply 3.0.0 staging --yes
python pipeline.py pending 3.0.0 prod
python pipeline.py approve 3.0.0 prod --role release-manager
python pipeline.py apply 3.0.0 prod --role release-manager --yes
# 预期错误：Version '3.0.0' must be in a signed package before release to prod.

# 测试 8: 发布到 prod 有签收包 - 成功
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py apply 2.0.0 staging --yes
python pipeline.py pending 1.0.0 prod
python pipeline.py pending 2.0.0 prod
python pipeline.py approve 1.0.0 prod --role release-manager
python pipeline.py approve 2.0.0 prod --role release-manager
python pipeline.py apply 1.0.0 prod --role release-manager --yes
# 预期：SUCCESS: Version 1.0.0 applied to prod

# 测试 9: 撤销签收后发布 - 失败
python pipeline.py package revoke prod-release-001 --role release-manager --reason "发现问题"
# 预期：Package 'prod-release-001' signoff revoked successfully
python pipeline.py apply 2.0.0 prod --role release-manager --yes
# 预期错误：Version '2.0.0' must be in a signed package before release to prod.

# 测试 10: 验证 prod current_version 没有推进（2.0.0 发布失败）
python pipeline.py history --type releases --env prod
# 预期：只有 1.0.0 -> prod (success)，没有 2.0.0 的记录
```

### 链路 14: 变更包导入导出与跨系统迁移
```bash
# 初始化环境
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json

# 创建并签收变更包
python pipeline.py package create export-test prod 1.0.0 2.0.0 --role release-manager
python pipeline.py package sign export-test --role release-manager

# 测试 1: 导出变更包为 JSON
python pipeline.py package export export-test --output export-test.json
# 预期：Package 'export-test' exported to export-test.json

# 测试 2: 验证导出文件结构
# export-test.json 应包含：package_name, target_environment, versions_list,
# config_summary, summary_hash, created_by, signoff_status, signed_by, signed_at 等

# 测试 3: 在新数据库中导入（缺失版本）- 失败
# 清理数据库
Remove-Item pipeline.db -Force
python pipeline.py init
# 只导入 v1，不导入 v2
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py package import export-test.json --role release-manager
# 预期错误：Version 2.0.0 not found in database

# 测试 4: 在新数据库中导入（版本完整）- 成功
python pipeline.py import config_pipeline/examples/config_v2.json
python pipeline.py package import export-test.json --role release-manager
# 预期：Package 'export-test' imported successfully

# 测试 5: 验证导入后包的完整性
python pipeline.py package verify export-test
# 预期：Package 'export-test' is valid

# 测试 6: 验证导入后包的签收状态
python pipeline.py package show export-test
# 预期显示 signoff_status: signed，signed_by 与导出时一致
```

### 链路 15: 变更包跨重启持久化验证
```bash
# 初始化环境并创建变更包
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py import config_pipeline/examples/config_v2.json
python pipeline.py package create persist-test staging 1.0.0 2.0.0
python pipeline.py package create persist-prod prod 1.0.0 --role release-manager
python pipeline.py package sign persist-prod --role release-manager

# 验证变更包存在
python pipeline.py package list > packages_before.txt
python pipeline.py package show persist-prod > pkg_detail_before.txt

# 模拟重启（不需要特殊操作，SQLite 已持久化）
# 关闭终端，重新打开，cd 到项目目录

# 验证变更包仍然存在
python pipeline.py package list > packages_after.txt
# packages_before.txt 和 packages_after.txt 内容应该一致

# 验证变更包详情仍然一致
python pipeline.py package show persist-prod > pkg_detail_after.txt
# pkg_detail_before.txt 和 pkg_detail_after.txt 内容应该一致（除时间戳外）

# 验证签收状态仍然生效
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py pending 1.0.0 prod
python pipeline.py approve 1.0.0 prod --role release-manager
python pipeline.py apply 1.0.0 prod --role release-manager --yes
# 预期成功：因为 persist-prod 包已签收

# 验证审计记录完整
python pipeline.py export --output pkg_persistence_audit.json --format json
# 预期包含所有 package 相关的操作记录：create, sign, verify, show 等
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
    │   ├── window_cmd.py       # window 命令（发布窗口管理）
    │   └── package_cmd.py      # package 命令（变更包签收管理）
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
- `releases`: 发布记录（成功/失败，含审批人、冲突原因、窗口覆盖原因）
- `rollbacks`: 回滚记录
- `audit_logs`: 所有操作的审计日志
- `error_logs`: 错误详情记录
- `environment_locks`: 环境锁定状态（锁定原因、锁定人、冲突原因）
- `approvals`: 审批记录（待审批/已审批/已拒绝，审批人、冲突原因）
- `previews`: 发布预演记录（包含目标配置快照、环境指针快照、锁状态快照、审批状态快照、变更摘要）
- `release_windows`: 发布窗口记录（环境、起止时间、原因、创建人、启用状态）
- `change_packages`: 变更包记录（包名、目标环境、版本清单、配置摘要、摘要哈希、创建人、签收状态、签收人、签收时间、撤销人、撤销时间、撤销原因）

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
- **窗口覆盖原因**: 发布窗口覆盖的具体原因和覆盖人
- **窗口事件**: window_blocked, window_overridden, window_override_denied 等事件记录
- **变更包事件**: package_create, package_sign, package_revoke, package_verify, package_export, package_import 等事件记录
- **变更包详情**: 包名、目标环境、版本清单、配置摘要哈希、签收状态、签收人、撤销原因
