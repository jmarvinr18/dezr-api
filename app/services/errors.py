"""Domain errors raised by the service layer.

Services never build HTTP responses; create_app() registers one handler that maps these
errors to status codes. That keeps the business logic usable from the CLI, jobs and tests.
"""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str, errors: dict | None = None, headers: dict | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors
        self.headers = headers or {}


class Unauthorized(ServiceError):
    status_code = 401


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class PayloadTooLarge(ServiceError):
    status_code = 413


class UnsupportedMediaType(ServiceError):
    status_code = 415


class ValidationFailed(ServiceError):
    status_code = 422


class TooManyRequests(ServiceError):
    status_code = 429

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message, headers={"Retry-After": str(retry_after)} if retry_after else None)


class NotImplementedHere(ServiceError):
    """The feature exists, but not with this server's setup (e.g. direct uploads with local files)."""

    status_code = 501


class StorageUnavailable(ServiceError):
    """The media store could not be reached or rejected the request."""

    status_code = 503


class NotConfigured(ServiceError):
    """A feature needs a setting the server doesn't have (e.g. SECRET_KEY for admin sign-in)."""

    status_code = 503


class AgentUnavailable(ServiceError):
    """The AI agent service could not be reached or rejected the request."""

    status_code = 503
