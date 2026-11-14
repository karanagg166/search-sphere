"""Reusable document intelligence with explicit partial results."""

from .config import PipelineConfig
from .errors import CapabilityUnavailable, IngestError, ResourceLimit
from .models import Chunk, IngestResult, NormalizedDocument, Status
from .pipeline import DocumentPipeline

__version__ = "0.2.0"
__all__ = [
    "DocumentPipeline",
    "PipelineConfig",
    "NormalizedDocument",
    "Chunk",
    "IngestResult",
    "Status",
    "IngestError",
    "CapabilityUnavailable",
    "ResourceLimit",
]
