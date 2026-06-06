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

## 核心约束

- 必填键: `app_name`, `version`, `features`, `database`, `api_endpoints`
- 合法环境: `dev`, `staging`, `prod`
- 版本在同一环境中不能重复发布
- 发布到 prod 前必须先在 staging 发布成功
- 失败时不会推进当前版本指针
- 所有操作记录保存在 SQLite 中

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

### 5. 执行发布
```bash
# 发布到 staging（跳过确认）
python pipeline.py apply 1.0.0 staging --yes

# 发布到 prod
python pipeline.py apply 1.0.0 prod --yes

# 发布 2.0.0 到 staging 和 prod
python pipeline.py apply 2.0.0 staging --yes
python pipeline.py apply 2.0.0 prod --yes
```

### 6. 查看历史
```bash
# 查看全部历史
python pipeline.py history

# 按环境筛选
python pipeline.py history --env staging

# 只看发布记录
python pipeline.py history --type releases
```

### 7. 回滚
```bash
# 回滚 staging 到 1.0.0
python pipeline.py rollback staging 1.0.0 --reason "feature rollback" --yes
```

### 8. 导出审计数据
```bash
# 导出为 JSON
python pipeline.py export --output audit.json --format json

# 导出为 Markdown
python pipeline.py export --output audit.md --format markdown

# 按环境导出
python pipeline.py export --env prod --output prod_audit.json
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

---

## 🔄 重启验证命令

验证数据持久化，重新运行命令后 history 保持一致：

```bash
# 第一步：执行一些操作
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py history > /tmp/history_before.txt

# 第二步：完全退出并重试
# （模拟重启 - 不需要做任何特殊操作，SQLite 已持久化）

# 第三步：验证历史记录一致
python pipeline.py history > /tmp/history_after.txt
diff /tmp/history_before.txt /tmp/history_after.txt
# 应该没有差异
```

或者更简单的验证：
```bash
# 初始发布
python pipeline.py init
python pipeline.py import config_pipeline/examples/config_v1.json
python pipeline.py apply 1.0.0 staging --yes
python pipeline.py history --type releases

# 关闭终端，重新打开
cd /path/to/project

# 验证历史仍然存在
python pipeline.py history --type releases
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
    │   └── export_cmd.py       # export 命令
    ├── utils/
    │   ├── __init__.py
    │   ├── errors.py           # 错误定义
    │   ├── database.py         # SQLite 数据层
    │   └── diff.py             # 差异计算
    └── examples/
        ├── config_v1.json      # 示例配置 v1
        ├── config_v2.json      # 示例配置 v2
        └── config_invalid.json # 无效配置（用于测试）
```

## 数据库结构

- `environments`: 环境信息和当前版本指针
- `configs`: 导入的配置快照
- `releases`: 发布记录（成功/失败）
- `rollbacks`: 回滚记录
- `audit_logs`: 所有操作的审计日志
- `error_logs`: 错误详情记录

## 审计导出内容

导出的审计文件包含：
- **计划摘要**: 每次发布的变更统计（新增/删除/修改数量）
- **操作者**: 当前系统用户名
- **时间戳**: 操作执行时间
- **错误原因**: 失败操作的具体错误信息
- **环境状态**: 各环境当前版本
