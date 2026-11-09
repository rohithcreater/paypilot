class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class BadRequest(DomainError):
    status_code = 400


class Unauthorized(DomainError):
    status_code = 401


class Forbidden(DomainError):
    status_code = 403


class NotFound(DomainError):
    status_code = 404


class TooManyRequests(DomainError):
    status_code = 429


class Upstream(DomainError):
    status_code = 502


class ServiceUnavailable(DomainError):
    status_code = 503


class PayPalError(Exception):
    """Raised for any failed PayPal API call. `issue` is PayPal's machine-readable code."""

    def __init__(self, status, issue, debug_id, message):
        super().__init__(message)
        self.status = status
        self.issue = issue
        self.debug_id = debug_id
        self.message = message
