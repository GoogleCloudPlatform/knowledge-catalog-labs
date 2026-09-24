""" Custom exception classes for handling specific error scenarios
    in the Dataplex Glossary Import utility."""
# --- Custom Exception Classes ---
class InvalidSpreadsheetURLError(Exception):
    """Raised when the provided spreadsheet URL is invalid."""
    pass

class InvalidGlossaryNameError(Exception):
    """Raised when the provided glossary name is invalid."""
    pass

class DataplexAPIError(Exception):
    """Raised when there is an error interacting with the Dataplex API."""
    pass

class SheetsAPIError(Exception):
    """Raised when there is an error interacting with the Google Sheets API."""
    pass

class NetworkError(Exception):
    """Raised when there is a persistent network connectivity issue."""
    pass

class NoCategoriesFoundError(Exception):
    """Raised when no categories are found for the given glossary."""
    pass

class NoTermsFoundError(Exception):
    """Raised when no terms are found for the given glossary."""
    pass

class InvalidTermNameError(Exception):
    """Raised when term name is invalid."""
    pass

class InvalidCategoryNameError(Exception):
    """Raised when Category name is invalid."""
    pass

class InvalidEntryIdFormatError(Exception):
    """Raised when the entry ID format is invalid."""
    pass

class InvalidTermIdentifierError(Exception):
    """Raised when a human-readable term identifier format is invalid."""
    pass

class TermNotFoundError(Exception):
    """Raised when a glossary term is not found by display name or ID."""
    pass

class AmbiguousTermError(TermNotFoundError):
    """Raised when multiple glossary terms share the same display name and no term ID is provided."""
    pass

class GlossaryNotFoundError(Exception):
    """Raised when a glossary is not found by display name."""
    pass

class EntryFQNNotFoundError(Exception):
    """Raised when a data asset entry cannot be found by its FQN."""
    pass
