"""Communication primitives for the Rigol DG1022."""

from .visa import VisaConnection, idn, list_resources

__all__ = ["VisaConnection", "idn", "list_resources"]
