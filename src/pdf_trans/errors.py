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


class MinerUConfigError(PDFTransError):
    """Raised when MinerU backend configuration is invalid."""


class WorkflowError(PDFTransError):
    """Raised when workflow input validation fails."""


class TranslationContentError(PDFTransError):
    """Raised when translation content cannot be read or written."""


class TranslationConfigError(PDFTransError):
    """Raised when translation environment configuration is incomplete."""


class TranslationClientError(PDFTransError):
    """Raised when one translation model call fails."""


class FormulaAuditError(PDFTransError):
    """Raised when a trustworthy formula audit cannot be produced."""
