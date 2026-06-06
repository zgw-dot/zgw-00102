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


class ReleaseWindowError(PipelineError):
    """Release window closed error"""
    def __init__(self, environment, window_info=None):
        self.environment = environment
        self.window_info = window_info
        window_part = ""
        if window_info:
            reason = window_info.get("reason", "")
            start_time = window_info.get("start_time", "")
            end_time = window_info.get("end_time", "")
            window_part = f" Reason: {reason}. Window: {start_time} to {end_time}"
        message = f"Environment '{environment}' is in a closed release window.{window_part}"
        super().__init__(message, code="RELEASE_WINDOW_CLOSED")


class OverridePermissionDeniedError(PipelineError):
    """Override window permission denied error"""
    def __init__(self, action, required_role, current_role=None):
        self.action = action
        self.required_role = required_role
        self.current_role = current_role
        role_part = f" Your role: {current_role}" if current_role else ""
        message = f"Permission denied to override release window for '{action}'. Required role: {required_role}.{role_part}"
        super().__init__(message, code="OVERRIDE_PERMISSION_DENIED")


class InvalidWindowTimeError(PipelineError):
    """Invalid release window time error"""
    def __init__(self, message):
        super().__init__(message, code="INVALID_WINDOW_TIME")


class OverlappingWindowError(PipelineError):
    """Overlapping release window error"""
    def __init__(self, environment, overlapping_windows=None):
        self.environment = environment
        self.overlapping_windows = overlapping_windows or []
        windows_str = "\n  - ".join([
            f"ID: {w['id']}, {w['start_time']} to {w['end_time']}"
            for w in overlapping_windows
        ]) if overlapping_windows else "unknown"
        message = f"Overlapping release window detected for environment '{environment}':\n  - {windows_str}"
        super().__init__(message, code="OVERLAPPING_WINDOW")


class WindowNotFoundError(PipelineError):
    """Release window not found error"""
    def __init__(self, window_id):
        self.window_id = window_id
        message = f"Release window with ID {window_id} not found"
        super().__init__(message, code="WINDOW_NOT_FOUND")


class PackageAlreadyExistsError(PipelineError):
    """Change package with this name already exists"""
    def __init__(self, package_name):
        self.package_name = package_name
        message = f"Change package '{package_name}' already exists"
        super().__init__(message, code="PACKAGE_ALREADY_EXISTS")


class PackageNotFoundError(PipelineError):
    """Change package not found"""
    def __init__(self, package_name):
        self.package_name = package_name
        message = f"Change package '{package_name}' not found"
        super().__init__(message, code="PACKAGE_NOT_FOUND")


class PackageVersionNotFoundError(PipelineError):
    """Version referenced in package does not exist"""
    def __init__(self, version):
        self.version = version
        message = f"Version '{version}' referenced in package not found in configs"
        super().__init__(message, code="PACKAGE_VERSION_NOT_FOUND")


class PackageSummaryMismatchError(PipelineError):
    """Package summary mismatch during import"""
    def __init__(self, package_name, expected_hash, actual_hash):
        self.package_name = package_name
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        message = f"Package '{package_name}' summary mismatch during import. Expected: {expected_hash[:12]}..., Actual: {actual_hash[:12]}..."
        super().__init__(message, code="PACKAGE_SUMMARY_MISMATCH")


class PackageNotSignedError(PipelineError):
    """Package must be signed before release to prod"""
    def __init__(self, package_name, version, environment):
        self.package_name = package_name
        self.version = version
        self.environment = environment
        message = f"Version '{version}' must be in a signed package '{package_name}' before release to {environment}. Use 'pipeline package sign' first."
        super().__init__(message, code="PACKAGE_NOT_SIGNED")


class PackageAlreadySignedError(PipelineError):
    """Package is already signed"""
    def __init__(self, package_name):
        self.package_name = package_name
        message = f"Package '{package_name}' is already signed"
        super().__init__(message, code="PACKAGE_ALREADY_SIGNED")


class PackageNotSignedForRevokeError(PipelineError):
    """Package is not signed, cannot revoke"""
    def __init__(self, package_name):
        self.package_name = package_name
        message = f"Package '{package_name}' is not signed, cannot revoke signoff"
        super().__init__(message, code="PACKAGE_NOT_SIGNED_FOR_REVOKE")


class InvalidPackageFormatError(PipelineError):
    """Invalid package format during import"""
    def __init__(self, reason):
        self.reason = reason
        message = f"Invalid package format: {reason}"
        super().__init__(message, code="INVALID_PACKAGE_FORMAT")


class ArchiveAlreadyExistsError(PipelineError):
    """Archive with this name already exists"""
    def __init__(self, archive_name):
        self.archive_name = archive_name
        message = f"Archive '{archive_name}' already exists"
        super().__init__(message, code="ARCHIVE_ALREADY_EXISTS")


class ArchiveNotFoundError(PipelineError):
    """Archive not found"""
    def __init__(self, archive_name):
        self.archive_name = archive_name
        message = f"Archive '{archive_name}' not found"
        super().__init__(message, code="ARCHIVE_NOT_FOUND")


class ArchiveNotSuccessfulReleaseError(PipelineError):
    """Cannot archive an unsuccessful release"""
    def __init__(self, version, environment):
        self.version = version
        self.environment = environment
        message = f"Version '{version}' has no successful release in '{environment}' environment"
        super().__init__(message, code="ARCHIVE_NOT_SUCCESSFUL_RELEASE")


class ArchiveMissingApprovalError(PipelineError):
    """Prod archive requires linked approval"""
    def __init__(self, version, environment):
        self.version = version
        self.environment = environment
        message = f"Archive for prod requires linked approval for version '{version}'"
        super().__init__(message, code="ARCHIVE_MISSING_APPROVAL")


class ArchiveSummaryMismatchError(PipelineError):
    """Archive summary mismatch during import"""
    def __init__(self, archive_name, expected_hash, actual_hash):
        self.archive_name = archive_name
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        message = f"Archive '{archive_name}' summary mismatch during import. Expected: {expected_hash[:12]}..., Actual: {actual_hash[:12]}..."
        super().__init__(message, code="ARCHIVE_SUMMARY_MISMATCH")


class ArchiveRevokedError(PipelineError):
    """Archive has been revoked"""
    def __init__(self, archive_name):
        self.archive_name = archive_name
        message = f"Archive '{archive_name}' has been revoked"
        super().__init__(message, code="ARCHIVE_REVOKED")


class ArchiveNotRevokedError(PipelineError):
    """Archive is not revoked, cannot verify as revoked"""
    def __init__(self, archive_name):
        self.archive_name = archive_name
        message = f"Archive '{archive_name}' is not revoked"
        super().__init__(message, code="ARCHIVE_NOT_REVOKED")


class InvalidArchiveFormatError(PipelineError):
    """Invalid archive format during import"""
    def __init__(self, reason):
        self.reason = reason
        message = f"Invalid archive format: {reason}"
        super().__init__(message, code="INVALID_ARCHIVE_FORMAT")


class ArchiveImportConflictError(PipelineError):
    """Archive name conflict during import"""
    def __init__(self, archive_name):
        self.archive_name = archive_name
        message = f"Archive '{archive_name}' already exists. Use --force to overwrite."
        super().__init__(message, code="ARCHIVE_IMPORT_CONFLICT")
