"""
Data models for Dataplex Glossary Import/Export operations.

These dataclasses provide type-safe representations of Dataplex resources
and eliminate the need for fragile dictionary access patterns.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EntryReference:
    """Represents a source or target reference within an EntryLink."""
    name: str
    path: str = ""
    type: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EntryReference':
        """Create an EntryReference from a dictionary."""
        return cls(
            name=data.get('name', ''),
            path=data.get('path', ''),
            type=data.get('type')
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        result = {'name': self.name}
        if self.path:
            result['path'] = self.path
        if self.type:
            result['type'] = self.type
        return result


@dataclass
class EntryLink:
    """Represents a complete EntryLink for import/export operations."""
    name: str
    entryLinkType: str
    entryReferences: List[EntryReference] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EntryLink':
        """Create EntryLink from a dictionary."""
        # Handle both nested and flat dictionary formats
        entry_data = data.get('entryLink', data)
        refs = [EntryReference.from_dict(ref) for ref in entry_data.get('entryReferences', [])]
        return cls(
            name=entry_data.get('name', ''),
            entryLinkType=entry_data.get('entryLinkType', ''),
            entryReferences=refs
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'entryLink': {
                'name': self.name,
                'entryLinkType': self.entryLinkType,
                'entryReferences': [ref.to_dict() for ref in self.entryReferences]
            }
        }


@dataclass
class ParsedTermIdentifier:
    """Represents a parsed human-readable term identifier."""
    project_id: str
    location: str
    glossary_display_name: str
    term_display_name: str


@dataclass
class SpreadsheetRow:
    """Represents a row from the EntryLink import spreadsheet."""
    entry_link_type: str = ""
    source_name: str = ""
    source_id: str = ""
    column: str = ""
    target_name: str = ""
    target_id: str = ""
    row_number: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SpreadsheetRow':
        """Create a SpreadsheetRow from a dictionary produced by sheet_utils."""
        def _value(key: str) -> str:
            return str(data.get(key) or '').strip()

        try:
            row_number = int(data.get('row_number') or 0)
        except (ValueError, TypeError):
            row_number = 0
        return cls(
            entry_link_type=_value('entry_link_type'),
            source_name=_value('source_name'),
            source_id=_value('source_id'),
            column=_value('column'),
            target_name=_value('target_name'),
            target_id=_value('target_id'),
            row_number=row_number,
        )
