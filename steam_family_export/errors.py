class ExportError(Exception):
    """An error safe to display without request URLs or credentials."""


class SchemaError(ExportError):
    """The API returned an unexpected shape or ambiguous attribution."""


class AuthenticationError(ExportError):
    """The user must obtain a new Steam web token."""
