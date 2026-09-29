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

class TransientAPIError(DataplexAPIError):
    """Raised when an API call kept failing with a network, 429 or 5xx error after all retries."""
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
    """Raised when a term reference is malformed or incomplete (e.g. the term ID is missing)."""
    pass

class TermNotFoundError(Exception):
    """Raised when no glossary term matches a term reference."""
    pass

class AmbiguousTermError(TermNotFoundError):
    """Raised when a term reference matches more than one glossary term."""
    pass

class GlossaryNotFoundError(Exception):
    """Raised when a glossary is not found by display name or ID."""
    pass

class EntryFQNNotFoundError(Exception):
    """Raised when a data asset entry cannot be found (or read) by its FQN."""
    pass
