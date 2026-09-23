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
class SpreadsheetRow:
    """Represents a row from the EntryLink import spreadsheet."""
    entry_link_type: str
    source_entry: str
    target_entry: str
    source_path: str = ""
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> 'SpreadsheetRow':
        """Create a SpreadsheetRow from a dictionary."""
        return cls(
            entry_link_type=data.get('entry_link_type', ''),
            source_entry=data.get('source_entry', ''),
            target_entry=data.get('target_entry', ''),
            source_path=data.get('source_path', '')
        )


@dataclass
class AspectRow:
    """Represents a row from the aspects sheet (Sheet 2).

    Sheet 2 has a normalized 3-column schema: (id, Aspect name, Aspect value).
    A single term/category may own many rows, one per aspect field.

    Attributes:
        term_id: Foreign key into the Sheet 1 'id' column.
        aspect_name: Aspect field identifier, e.g. 'custom-gov.tier'.
        aspect_value: Raw (unparsed) cell value.
        row_number: 1-based spreadsheet row number, used in validation messages.
    """
    term_id: str = ""
    aspect_name: str = ""
    aspect_value: str = ""
    row_number: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, str], row_number: int = 0) -> 'AspectRow':
        """Create an AspectRow from a dictionary, tolerating header spellings.

        Args:
            data: Mapping of column header to cell value. Both the canonical
                headers ('id', 'Aspect name', 'Aspect value') and snake_case
                variants ('term_id', 'aspect_name', 'aspect_value') are accepted.
            row_number: 1-based spreadsheet row number for error reporting.

        Returns:
            A populated AspectRow with all string fields stripped.
        """
        term_id = (
            data.get('id') or data.get('term_id') or data.get('ID') or ''
        ).strip()
        aspect_name = (
            data.get('Aspect name') or data.get('aspect_name')
            or data.get('aspect name') or data.get('Aspect Name') or ''
        ).strip()
        aspect_value = (
            data.get('Aspect value') or data.get('aspect_value')
            or data.get('aspect value') or data.get('Aspect Value') or ''
        )
        return cls(
            term_id=term_id,
            aspect_name=aspect_name,
            aspect_value=aspect_value.strip() if isinstance(aspect_value, str) else aspect_value,
            row_number=row_number,
        )

    def to_row(self) -> List[str]:
        """Convert to a spreadsheet row [id, Aspect name, Aspect value]."""
        return [self.term_id, self.aspect_name, self.aspect_value]

    def is_empty(self) -> bool:
        """Return True when the row carries no usable data."""
        return not (self.term_id or self.aspect_name or self.aspect_value)


@dataclass
class ParsedAspectIdentifier:
    """Represents a parsed 'Aspect name' cell.

    Attributes:
        project_id: Project owning the AspectType (defaulted to the glossary's).
        location: Location of the AspectType (defaulted to the glossary's).
        aspect_type_id: AspectType id, e.g. 'custom-gov'.
        field_name: Field within the aspect, possibly dotted for record
            sub-fields, e.g. 'tier' or 'owner.email'.
    """
    project_id: str
    location: str
    aspect_type_id: str
    field_name: str

    @property
    def aspect_type_resource(self) -> str:
        """Full AspectType resource name."""
        return (
            f"projects/{self.project_id}/locations/{self.location}"
            f"/aspectTypes/{self.aspect_type_id}"
        )

    @property
    def aspect_key(self) -> str:
        """Key used in an Entry's 'aspects' map, e.g. 'my-proj.us-central1.custom-gov'."""
        return f"{self.project_id}.{self.location}.{self.aspect_type_id}"

    @property
    def field_path(self) -> List[str]:
        """Field name split into its dotted segments."""
        return [segment for segment in self.field_name.split('.') if segment]

