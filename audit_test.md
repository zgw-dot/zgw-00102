# Configuration Pipeline Audit Export

**Exported At:** 2026-06-06T11:37:40.348415
**Exported By:** 13623

## Environment Status

| Environment | Current Version | Updated At |
|-------------|-----------------|------------|
| dev | None | 2026-06-06 03:32:45 |
| prod | 1.0.0 | 2026-06-06 03:34:13 |
| staging | 2.0.0 | 2026-06-06 03:34:11 |

## Environment Lock Status

| Environment | Status | Lock Reason | Locked By | Locked At | Conflict Reason |
|-------------|--------|-------------|-----------|-----------|-----------------|
| dev | UNLOCKED | N/A | N/A | N/A | N/A |
| prod | UNLOCKED | N/A | N/A | N/A | N/A |
| staging | UNLOCKED | N/A | N/A | N/A | N/A |

## Approvals

| ID | Version | Env | Status | Requested By | Requested At | Approved By | Approved At | Conflict Reason |
|----|---------|-----|--------|--------------|--------------|-------------|-------------|-----------------|
| 2 | 2.0.0 | prod | approved | 13623 | 2026-06-06 03:34:11 | 13623 | 2026-06-06 03:34:12 | N/A |
| 1 | 1.0.0 | prod | approved | 13623 | 2026-06-06 03:32:46 | 13623 | 2026-06-06 03:32:46 | N/A |

## Deployment Plan Summaries

| ID | Version | Env | Status | Operator | Approved By | Applied At | Changes | Conflict Reason |
|----|---------|-----|--------|----------|-------------|------------|---------|-----------------|
| 5 | 1.0.0 | prod | success | 13623 | N/A | 2026-06-06 03:34:13 | +0 -0 ~10 | N/A |
| 4 | 2.0.0 | prod | success | 13623 | 13623 | 2026-06-06 03:34:12 | +0 -0 ~10 | N/A |
| 3 | 2.0.0 | staging | success | 13623 | N/A | 2026-06-06 03:34:11 | +0 -0 ~10 | N/A |
| 2 | 1.0.0 | prod | success | 13623 | 13623 | 2026-06-06 03:32:46 | +12 -0 ~0 | N/A |
| 1 | 1.0.0 | staging | success | 13623 | N/A | 2026-06-06 03:32:45 | +12 -0 ~0 | N/A |

## Audit Log

| ID | Action | Env | Version | Status | Operator | Timestamp | Error Reason | Conflict Reason |
|----|--------|-----|---------|--------|----------|-----------|--------------|-----------------|
| 22 | lock-status | N/A | N/A | success | 13623 | 2026-06-06 03:37:40 |  | N/A |
| 21 | pending-list | N/A | N/A | success | 13623 | 2026-06-06 03:37:39 |  | N/A |
| 20 | export | N/A | N/A | success | 13623 | 2026-06-06 03:37:29 |  | N/A |
| 19 | rollback | prod | 1.0.0 | success | 13623 | 2026-06-06 03:34:13 |  | N/A |
| 18 | unlock | prod | N/A | success | 13623 | 2026-06-06 03:34:13 |  | N/A |
| 17 | rollback | prod | 1.0.0 | failed | 13623 | 2026-06-06 03:34:13 | Environment 'prod' is locked. Reason: 维护中 (locked by 13623) | Environment 'prod' is locked. Reason: 维护中 (locked by 13623) |
| 16 | lock | prod | N/A | success | 13623 | 2026-06-06 03:34:13 |  | N/A |
| 15 | apply | prod | 2.0.0 | success | 13623 | 2026-06-06 03:34:12 |  | N/A |
| 14 | unlock | prod | N/A | success | 13623 | 2026-06-06 03:34:12 |  | N/A |
| 13 | apply | prod | 2.0.0 | failed | 13623 | 2026-06-06 03:34:12 | Environment 'prod' is locked. Reason: 紧急冻结 (locked by 13623) | Environment 'prod' is locked. Reason: 紧急冻结 (locked by 13623) |
| 12 | lock | prod | N/A | success | 13623 | 2026-06-06 03:34:12 |  | N/A |
| 11 | approve | prod | 2.0.0 | success | 13623 | 2026-06-06 03:34:12 |  | N/A |
| 10 | approve | prod | 2.0.0 | failed | 13623 | 2026-06-06 03:34:11 | Permission denied for 'approve'. Required role: release-manager. Your role: developer | N/A |
| 9 | pending | prod | 2.0.0 | success | 13623 | 2026-06-06 03:34:11 |  | N/A |
| 8 | apply | staging | 2.0.0 | success | 13623 | 2026-06-06 03:34:11 |  | N/A |
| 7 | apply | prod | 1.0.0 | success | 13623 | 2026-06-06 03:32:46 |  | N/A |
| 6 | approve | prod | 1.0.0 | success | 13623 | 2026-06-06 03:32:46 |  | N/A |
| 5 | pending | prod | 1.0.0 | success | 13623 | 2026-06-06 03:32:46 |  | N/A |
| 4 | apply | staging | 1.0.0 | success | 13623 | 2026-06-06 03:32:45 |  | N/A |
| 3 | import | N/A | 2.0.0 | success | 13623 | 2026-06-06 03:32:45 |  | N/A |
| 2 | import | N/A | 1.0.0 | success | 13623 | 2026-06-06 03:32:45 |  | N/A |
| 1 | init | N/A | N/A | success | 13623 | 2026-06-06 03:32:45 |  | N/A |

## Error Logs

| ID | Command | Error Code | Message | Env | Version | Operator | Timestamp |
|----|---------|------------|---------|-----|---------|----------|-----------|
| 3 | rollback | ENVIRONMENT_LOCKED | Environment 'prod' is locked. Reason: 维护中 (locked ... | prod | 1.0.0 | 13623 | 2026-06-06 03:34:13 |
| 2 | apply | ENVIRONMENT_LOCKED | Environment 'prod' is locked. Reason: 紧急冻结 (locked... | prod | 2.0.0 | 13623 | 2026-06-06 03:34:12 |
| 1 | approve | PERMISSION_DENIED | Permission denied for 'approve'. Required role: re... | prod | 2.0.0 | 13623 | 2026-06-06 03:34:11 |

## Rollbacks

| ID | Env | From | To | Reason | Operator | Timestamp |
|----|-----|------|----|--------|----------|-----------|
| 1 | prod | 2.0.0 | 1.0.0 | 回滚 | 13623 | 2026-06-06 03:34:13 |
