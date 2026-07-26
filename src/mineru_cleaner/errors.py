class MinerUCleanerError(Exception):
    """Base exception for expected application failures."""


class ContentListError(MinerUCleanerError):
    """Raised when a content-list file cannot be processed."""


class ArchiveError(MinerUCleanerError):
    """Raised when a MinerU result archive cannot be processed."""


class MinerUClientError(MinerUCleanerError):
    """Raised when communication with MinerU fails."""


class WorkflowError(MinerUCleanerError):
    """Raised when workflow input validation fails."""
