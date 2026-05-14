class StudyLensError(Exception):
    """Base exception for expected StudyLens failures."""


class ConfigurationError(StudyLensError):
    """Raised when configuration is missing or inconsistent."""


class IngestionError(StudyLensError):
    """Raised when an external source cannot be ingested."""


class UnsupportedDocumentError(IngestionError):
    """Raised when a file type cannot be converted to text."""

