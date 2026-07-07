from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UploadResult:
    platform: str
    post_id: str
    url: str


class PlatformUploader(ABC):
    name: str

    @abstractmethod
    def upload(self, video_path: Path, title: str, description: str) -> UploadResult:
        raise NotImplementedError

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError
