"""OTel raw store — append OTLP batches and query spans."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message

from factory.otel.paths import (
    factory_otel_db_path,
    factory_otel_jsonl_path,
    factory_otel_state_dir,
)
from factory.otel.reader import OtelRawReader
from factory.otel.scrub_otlp import scrub_otlp_dict


class OtelRawStore:
    def __init__(
        self,
        *,
        jsonl_path: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._jsonl_path = jsonl_path or factory_otel_jsonl_path()
        self._db_path = db_path or factory_otel_db_path()
        self._reader = OtelRawReader(jsonl_path=self._jsonl_path, db_path=self._db_path)
        self._lock = threading.Lock()

    @property
    def reader(self) -> OtelRawReader:
        return self._reader

    def ensure_dirs(self) -> None:
        factory_otel_state_dir().mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    def append_otlp(self, message: Message) -> int:
        """Persist an OTLP export batch to JSONL and index new rows."""
        payload: dict[str, Any] = MessageToDict(
            message,
            preserving_proto_field_name=False,
        )
        scrub_otlp_dict(payload)
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        with self._lock:
            self.ensure_dirs()
            with self._jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            return self._reader.index_jsonl(force=True)