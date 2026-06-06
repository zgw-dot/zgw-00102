# Configuration Pipeline Audit Export

**Exported At:** 2026-06-06T12:31:18.372815
**Exported By:** 13623

## Environment Status

| Environment | Current Version | Updated At |
|-------------|-----------------|------------|
| dev | None | 2026-06-06 04:30:45 |
| prod | None | 2026-06-06 04:30:45 |
| staging | 2.0.0 | 2026-06-06 04:30:47 |

## Environment Lock Status

| Environment | Status | Lock Reason | Locked By | Locked At | Conflict Reason |
|-------------|--------|-------------|-----------|-----------|-----------------|
| dev | UNLOCKED | N/A | N/A | N/A | N/A |
| prod | LOCKED | 回归测试锁定 | 13623 | 2026-06-06 04:30:46 | N/A |
| staging | UNLOCKED | N/A | N/A | N/A | N/A |

## Approvals

| ID | Version | Env | Status | Requested By | Requested At | Approved By | Approved At | Conflict Reason |
|----|---------|-----|--------|--------------|--------------|-------------|-------------|-----------------|
| 2 | 1.0.0 | prod | approved | 13623 | 2026-06-06 04:31:16 | 13623 | 2026-06-06 04:31:17 | N/A |
| 1 | 2.0.0 | prod | pending | 13623 | 2026-06-06 04:30:47 | N/A | N/A | N/A |

## Deployment Plan Summaries

| ID | Version | Env | Status | Operator | Approved By | Applied At | Changes | Conflict Reason |
|----|---------|-----|--------|----------|-------------|------------|---------|-----------------|
| 2 | 2.0.0 | staging | success | 13623 | N/A | 2026-06-06 04:30:47 | +0 -0 ~10 | N/A |
| 1 | 1.0.0 | staging | success | 13623 | N/A | 2026-06-06 04:30:46 | +12 -0 ~0 | N/A |

## Audit Log

| ID | Action | Env | Version | Status | Operator | Timestamp | Error Reason | Conflict Reason |
|----|--------|-----|---------|--------|----------|-----------|--------------|-----------------|
| 14 | export | N/A | N/A | success | 13623 | 2026-06-06 04:31:17 |  | N/A |
| 13 | apply | prod | 1.0.0 | failed | 13623 | 2026-06-06 04:31:17 | Environment 'prod' is locked. Reason: 回归测试锁定 (locked by 13623) | Environment 'prod' is locked. Reason: 回归测试锁定 (locked by 13623) |
| 12 | approve | prod | 1.0.0 | success | 13623 | 2026-06-06 04:31:17 |  | N/A |
| 11 | pending | prod | 1.0.0 | success | 13623 | 2026-06-06 04:31:16 |  | N/A |
| 10 | history | N/A | N/A | success | 13623 | 2026-06-06 04:30:48 |  | N/A |
| 9 | pending-list | N/A | N/A | success | 13623 | 2026-06-06 04:30:47 |  | N/A |
| 8 | pending | prod | 2.0.0 | success | 13623 | 2026-06-06 04:30:47 |  | N/A |
| 7 | apply | staging | 2.0.0 | success | 13623 | 2026-06-06 04:30:47 |  | N/A |
| 6 | apply | prod | 2.0.0 | failed | 13623 | 2026-06-06 04:30:46 | Version 2.0.0 must be deployed to staging before prod | Version 2.0.0 must be deployed to staging before prod |
| 5 | lock | prod | N/A | success | 13623 | 2026-06-06 04:30:46 |  | N/A |
| 4 | apply | staging | 1.0.0 | success | 13623 | 2026-06-06 04:30:46 |  | N/A |
| 3 | import | N/A | 2.0.0 | success | 13623 | 2026-06-06 04:30:45 |  | N/A |
| 2 | import | N/A | 1.0.0 | success | 13623 | 2026-06-06 04:30:45 |  | N/A |
| 1 | init | N/A | N/A | success | 13623 | 2026-06-06 04:30:45 |  | N/A |

## Error Logs

| ID | Command | Error Code | Message | Env | Version | Operator | Timestamp |
|----|---------|------------|---------|-----|---------|----------|-----------|
| 2 | apply | ENVIRONMENT_LOCKED | Environment 'prod' is locked. Reason: 回归测试锁定 (lock... | prod | 1.0.0 | 13623 | 2026-06-06 04:31:17 |
| 1 | apply | STAGING_REQUIRED | Version 2.0.0 must be deployed to staging before p... | prod | 2.0.0 | 13623 | 2026-06-06 04:30:46 |
