from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path


@dataclass
class UploadRecord:
    file_name: str
    file_hash: str
    uploaded_at: str
    title: str
    video_id: str = ""
    platform_ids: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.platform_ids is None:
            self.platform_ids = {}
        if self.video_id and "youtube" not in self.platform_ids:
            self.platform_ids["youtube"] = self.video_id


class UploadState:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, UploadRecord] = {}
        self._recycle_index = 0
        self._load()

    def _load(self) -> None:
        if not self.state_file.exists():
            return
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        for item in data.get("uploads", []):
            if "platform_ids" not in item or item["platform_ids"] is None:
                item["platform_ids"] = {}
                if item.get("video_id"):
                    item["platform_ids"] = {"youtube": item["video_id"]}
            record = UploadRecord(**item)
            self._records[record.file_hash] = record
        self._recycle_index = int(data.get("recycle_index", 0))

    def save(self) -> None:
        payload = {
            "uploads": [asdict(record) for record in self._records.values()],
            "recycle_index": self._recycle_index,
        }
        self.state_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def file_hash(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _record_for(self, path: Path) -> UploadRecord | None:
        return self._records.get(self.file_hash(path))

    def is_uploaded(self, path: Path) -> bool:
        return self.file_hash(path) in self._records

    def is_uploaded_to_platform(self, path: Path, platform: str) -> bool:
        record = self._record_for(path)
        if not record or not record.platform_ids:
            return False
        return platform in record.platform_ids

    def is_fully_uploaded(self, path: Path, platforms: list[str]) -> bool:
        record = self._record_for(path)
        if not record or not record.platform_ids:
            return False
        return all(platform in record.platform_ids for platform in platforms)

    def mark_uploaded(self, path: Path, video_id: str, title: str) -> None:
        self.mark_platform_upload(path, "youtube", video_id, title)

    def mark_platform_upload(
        self,
        path: Path,
        platform: str,
        post_id: str,
        title: str,
    ) -> None:
        file_hash = self.file_hash(path)
        record = self._records.get(file_hash)
        if record is None:
            record = UploadRecord(
                file_name=path.name,
                file_hash=file_hash,
                uploaded_at=datetime.now(timezone.utc).isoformat(),
                title=title,
                platform_ids={},
            )
        record.platform_ids[platform] = post_id
        if platform == "youtube":
            record.video_id = post_id
        record.uploaded_at = datetime.now(timezone.utc).isoformat()
        self._records[file_hash] = record
        self.save()

    def next_recycle_video(
        self,
        uploaded_dir: Path,
        video_extensions: tuple[str, ...],
    ) -> Path | None:
        if not uploaded_dir.exists():
            return None

        videos = sorted(
            (
                path
                for path in uploaded_dir.iterdir()
                if path.is_file() and path.suffix.lower() in video_extensions
            ),
            key=lambda path: path.name.lower(),
        )
        if not videos:
            return None

        index = self._recycle_index % len(videos)
        self._recycle_index = (self._recycle_index + 1) % len(videos)
        self.save()
        return videos[index]
