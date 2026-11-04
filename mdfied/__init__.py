"""mdfied — turn PDFs, PowerPoint decks and videos into clean, chunked Markdown."""

from .pdf import pdf_to_markdown
from .pptx import pptx_to_markdown
from .video import video_to_markdown

__all__ = ["pdf_to_markdown", "pptx_to_markdown", "video_to_markdown"]
__version__ = "0.2.0"
