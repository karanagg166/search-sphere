class SearchSphereError(Exception):
    """Base exception for Search-Sphere client errors."""
    pass


class AuthenticationError(SearchSphereError):
    """Raised when authentication fails (401)."""
    pass


class AuthorizationError(SearchSphereError):
    """Raised when access is forbidden or client lacks required scopes (403)."""
    pass


class NotFoundError(SearchSphereError):
    """Raised when requested resource does not exist (404)."""
    pass


class ConflictError(SearchSphereError):
    """Raised on conflict, e.g. duplicate collection (409)."""
    pass


class ValidationError(SearchSphereError):
    """Raised when request payload or parameters are invalid (400/422)."""
    pass


class ServerError(SearchSphereError):
    """Raised on 5xx server errors."""
    pass
