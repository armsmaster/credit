class AppError(Exception):
    """Expected business error; rendered as JSON {"detail": ..., "code": ...}."""

    status_code = 400
    code = "bad_request"

    def __init__(self, detail: str, *, code: str | None = None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"
