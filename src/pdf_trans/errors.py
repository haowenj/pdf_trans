class PDFTransError(Exception):
    """Base exception for expected application failures."""


class ContentListError(PDFTransError):
    """Raised when a content-list file cannot be processed."""


class NormalizationError(PDFTransError):
    """Raised when cross-page normalization validation fails."""


class ArchiveError(PDFTransError):
    """Raised when a MinerU result archive cannot be processed."""


class MinerUClientError(PDFTransError):
    """Raised when communication with MinerU fails."""


class WorkflowError(PDFTransError):
    """Raised when workflow input validation fails."""
