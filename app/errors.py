class AppError(Exception):
    """Base class for domain-specific errors."""


class ValidationError(AppError):
    pass


class NotFoundError(AppError):
    pass


class ConflictError(AppError):
    pass
