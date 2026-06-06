class PipelineError(Exception):
    """Base class for pipeline errors"""
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code
        self.message = message


class ValidationError(PipelineError):
    """Configuration validation error"""
    def __init__(self, message, missing_keys=None, invalid_keys=None):
        super().__init__(message, code="VALIDATION_ERROR")
        self.missing_keys = missing_keys or []
        self.invalid_keys = invalid_keys or []


class EnvironmentError(PipelineError):
    """Invalid environment error"""
    def __init__(self, env, valid_envs=None):
        self.env = env
        self.valid_envs = valid_envs or ["dev", "staging", "prod"]
        message = f"Invalid environment '{env}'. Must be one of: {', '.join(self.valid_envs)}"
        super().__init__(message, code="INVALID_ENVIRONMENT")


class DuplicateVersionError(PipelineError):
    """Duplicate version error"""
    def __init__(self, version, env):
        self.version = version
        self.env = env
        message = f"Version {version} already exists in {env} environment"
        super().__init__(message, code="DUPLICATE_VERSION")


class StagingRequiredError(PipelineError):
    """Staging deployment required before prod error"""
    def __init__(self, version):
        self.version = version
        message = f"Version {version} must be deployed to staging before prod"
        super().__init__(message, code="STAGING_REQUIRED")


class VersionNotFoundError(PipelineError):
    """Version not found error"""
    def __init__(self, version, env=None):
        self.version = version
        self.env = env
        if env:
            message = f"Version {version} not found in {env} environment"
        else:
            message = f"Version {version} not found"
        super().__init__(message, code="VERSION_NOT_FOUND")


class NoChangesError(PipelineError):
    """No changes detected in plan"""
    def __init__(self):
        message = "No changes detected between current and target configuration"
        super().__init__(message, code="NO_CHANGES")


class PipelineNotInitializedError(PipelineError):
    """Pipeline not initialized error"""
    def __init__(self):
        message = "Pipeline not initialized. Run 'pipeline init' first."
        super().__init__(message, code="NOT_INITIALIZED")


class EnvironmentLockedError(PipelineError):
    """Environment is locked error"""
    def __init__(self, environment, lock_reason=None, locked_by=None):
        self.environment = environment
        self.lock_reason = lock_reason
        self.locked_by = locked_by
        reason_part = f" Reason: {lock_reason}" if lock_reason else ""
        by_part = f" (locked by {locked_by})" if locked_by else ""
        message = f"Environment '{environment}' is locked.{reason_part}{by_part}"
        super().__init__(message, code="ENVIRONMENT_LOCKED")


class ApprovalRequiredError(PipelineError):
    """Approval required before release error"""
    def __init__(self, version, environment):
        self.version = version
        self.environment = environment
        message = f"Version {version} requires approval before releasing to {environment}. Use 'pipeline approve' first."
        super().__init__(message, code="APPROVAL_REQUIRED")


class PermissionDeniedError(PipelineError):
    """Permission denied error"""
    def __init__(self, action, required_role, current_role=None):
        self.action = action
        self.required_role = required_role
        self.current_role = current_role
        role_part = f" Your role: {current_role}" if current_role else ""
        message = f"Permission denied for '{action}'. Required role: {required_role}.{role_part}"
        super().__init__(message, code="PERMISSION_DENIED")


class InvalidRoleError(PipelineError):
    """Invalid role error"""
    def __init__(self, role, valid_roles=None):
        self.role = role
        self.valid_roles = valid_roles or ["developer", "release-manager"]
        message = f"Invalid role '{role}'. Must be one of: {', '.join(self.valid_roles)}"
        super().__init__(message, code="INVALID_ROLE")


class ApprovalNotFoundError(PipelineError):
    """Approval not found error"""
    def __init__(self, version, environment):
        self.version = version
        self.environment = environment
        message = f"No pending approval found for version {version} in {environment} environment"
        super().__init__(message, code="APPROVAL_NOT_FOUND")


class AlreadyApprovedError(PipelineError):
    """Already approved error"""
    def __init__(self, version, environment):
        self.version = version
        self.environment = environment
        message = f"Version {version} is already approved for {environment} environment"
        super().__init__(message, code="ALREADY_APPROVED")


class PendingApprovalExistsError(PipelineError):
    """Pending approval already exists error"""
    def __init__(self, version, environment):
        self.version = version
        self.environment = environment
        message = f"A pending approval already exists for version {version} in {environment} environment"
        super().__init__(message, code="PENDING_APPROVAL_EXISTS")


class EnvironmentNotLockedError(PipelineError):
    """Environment is not locked error"""
    def __init__(self, environment):
        self.environment = environment
        message = f"Environment '{environment}' is not locked"
        super().__init__(message, code="ENVIRONMENT_NOT_LOCKED")


class AlreadyLockedError(PipelineError):
    """Environment already locked error"""
    def __init__(self, environment, lock_reason=None, locked_by=None):
        self.environment = environment
        self.lock_reason = lock_reason
        self.locked_by = locked_by
        reason_part = f" Reason: {lock_reason}" if lock_reason else ""
        by_part = f" (locked by {locked_by})" if locked_by else ""
        message = f"Environment '{environment}' is already locked.{reason_part}{by_part}"
        super().__init__(message, code="ALREADY_LOCKED")


class PreviewNotFoundError(PipelineError):
    """Preview not found error"""
    def __init__(self, version=None, environment=None):
        self.version = version
        self.environment = environment
        if version and environment:
            message = f"No preview found for version '{version}' in environment '{environment}'"
        else:
            message = "No preview found"
        super().__init__(message, code="PREVIEW_NOT_FOUND")


class PreviewDriftError(PipelineError):
    """Preview drift detected error"""
    def __init__(self, drift_reasons):
        self.drift_reasons = drift_reasons
        reasons_str = "\n  - ".join(drift_reasons)
        message = f"Preview drift detected. State has changed since preview:\n  - {reasons_str}"
        super().__init__(message, code="PREVIEW_DRIFT")


class PreviewAckDeniedError(PipelineError):
    """Preview drift acknowledgment denied error"""
    def __init__(self, reason):
        self.reason = reason
        message = f"Cannot acknowledge drift: {reason}. Only release-manager can acknowledge drift."
        super().__init__(message, code="PREVIEW_ACK_DENIED")


class PreviewNoChangesError(PipelineError):
    """Preview has no changes error"""
    def __init__(self):
        message = "Preview shows no changes between current and target configuration"
        super().__init__(message, code="PREVIEW_NO_CHANGES")
