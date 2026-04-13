from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel

from .models import LocalPreprocessingResult, WarningItem
from .preprocessing_report import write_preprocessing_report


LOCAL_PREPROCESSING_STAGE_DIR = "stage_01_local_preprocessing"


def generate_run_id(video_path: Path) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = re.sub(r"[^a-z0-9]", "_", video_path.stem.lower())[:20]
    suffix = hashlib.sha1(str(video_path.expanduser().resolve()).encode("utf-8")).hexdigest()[:6]
    return f"{timestamp}_{stem}_{suffix}"


class StageArtifacts:
    def __init__(self, stage_dir: Path):
        self.stage_dir = stage_dir
        self.stage_dir.mkdir(parents=True, exist_ok=True)

    def write_output(self, data: BaseModel) -> Path:
        output_path = self.stage_dir / "output.json"
        output_path.write_text(data.model_dump_json(indent=2), encoding="utf-8")
        return output_path

    def write_warnings(self, *, stage: str, warnings: Sequence[WarningItem]) -> Path:
        warnings_path = self.stage_dir / "warnings.json"
        payload = {
            "stage": stage,
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "warnings": [item.model_dump(mode="json") for item in warnings],
        }
        warnings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return warnings_path

    def write_report(
        self,
        result: LocalPreprocessingResult,
        *,
        title: str | None = None,
        warnings: Sequence[WarningItem] | None = None,
    ) -> Path:
        return write_preprocessing_report(
            result,
            self.stage_dir / "report.html",
            title=title,
            warnings=warnings,
        )


def write_local_preprocessing_artifacts(
    result: LocalPreprocessingResult,
    *,
    runs_dir: Path = Path("runs"),
    run_id: str | None = None,
    warnings: Sequence[WarningItem] | None = None,
    report_title: str | None = None,
) -> Path:
    actual_run_id = run_id or generate_run_id(Path(result.video_path))
    stage_dir = runs_dir / actual_run_id / LOCAL_PREPROCESSING_STAGE_DIR
    artifacts = StageArtifacts(stage_dir)
    artifacts.write_output(result)
    artifacts.write_warnings(stage=LOCAL_PREPROCESSING_STAGE_DIR, warnings=list(warnings or []))
    artifacts.write_report(result, title=report_title, warnings=warnings)
    return stage_dir
