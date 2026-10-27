"""Errors the repository raises. The API maps them to HTTP status codes."""


class RepositoryError(Exception):
    """Base class for errors the API maps to HTTP status codes."""


class NotFound(RepositoryError):
    pass


class Conflict(RepositoryError):
    pass


class Invalid(RepositoryError, ValueError):
    pass
