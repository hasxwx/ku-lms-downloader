"""kulms — Korea University LMS link downloader.

Paste an LMS course/module/assignment link and download its attachments
and weekly-study (주차학습) videos. Standalone, standard-library only.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .client import LmsClient
from .models import Attachment, Course, Material
from .urls import LmsTarget, parse_lms_url

__all__ = [
    "LmsClient",
    "Attachment",
    "Course",
    "Material",
    "LmsTarget",
    "parse_lms_url",
]
