"""Cloud storage exceptions safe to display without exposing credentials."""

from __future__ import annotations


class CloudStorageError(RuntimeError):
    """Base error for cloud storage operations."""


class CloudAuthError(CloudStorageError):
    """Authentication failed or no user credential is available."""


class CloudCredentialExpiredError(CloudAuthError):
    """A refreshed credential was still rejected by the remote service."""


class CloudNetworkError(CloudStorageError):
    """The remote service could not be reached."""


class CloudTimeoutError(CloudNetworkError):
    """A cloud operation timed out."""


class CloudRemoteApiError(CloudStorageError):
    """The remote service returned an unsuccessful response."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class CloudNotFoundError(CloudRemoteApiError):
    """The requested remote item does not exist."""


class CloudConflictError(CloudRemoteApiError):
    """The destination already exists."""
