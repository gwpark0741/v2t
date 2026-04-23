from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from v2t_prototype import pipeline_automation


def test_discover_video_paths_filters_and_sorts(tmp_path: Path):
    videos_dir = tmp_path / "videos"
    nested_dir = videos_dir / "nested"
    nested_dir.mkdir(parents=True)
    (videos_dir / "b.mp4").write_text("b", encoding="utf-8")
    (videos_dir / "ignore.txt").write_text("x", encoding="utf-8")
    (nested_dir / "a.mov").write_text("a", encoding="utf-8")

    non_recursive = pipeline_automation.discover_video_paths([videos_dir], recursive=False)
    recursive = pipeline_automation.discover_video_paths([videos_dir], recursive=True)

    assert [path.name for path in non_recursive] == ["b.mp4"]
    assert [path.name for path in recursive] == ["b.mp4", "a.mov"]


def test_run_pipeline_for_video_executes_all_stages_and_generates_report(tmp_path: Path, monkeypatch):
    video_path = tmp_path / "sample.mp4"
    video_path.write_text("video", encoding="utf-8")
    runs_dir = tmp_path / "runs"
    calls: list[str] = []

    local = SimpleNamespace(name="local")
    full_video_asset = SimpleNamespace(name="full")
    agent_a_output = SimpleNamespace(response=SimpleNamespace(entity_registry="registry"))
    segment_prep = SimpleNamespace(name="segment")
    agent_b_result = SimpleNamespace(name="agent_b")
    agent_c_result = SimpleNamespace(name="agent_c")
    fake_client = object()

    monkeypatch.setattr(pipeline_automation, "create_gemini_client", lambda: fake_client)
    monkeypatch.setattr(
        pipeline_automation,
        "run_local_preprocessing",
        lambda path: calls.append(f"stage1:{Path(path).name}") or local,
    )
    monkeypatch.setattr(
        pipeline_automation,
        "collect_local_preprocessing_warnings",
        lambda result: calls.append("warnings") or ["warning"],
    )
    monkeypatch.setattr(
        pipeline_automation,
        "write_local_preprocessing_artifacts",
        lambda *args, **kwargs: calls.append("write_stage1"),
    )
    monkeypatch.setattr(
        pipeline_automation,
        "prepare_full_video_asset",
        lambda result, client=None: calls.append(f"stage2:{client is fake_client}") or full_video_asset,
    )
    monkeypatch.setattr(
        pipeline_automation,
        "write_full_video_asset_artifacts",
        lambda *args, **kwargs: calls.append("write_stage2"),
    )
    monkeypatch.setattr(
        pipeline_automation,
        "run_agent_a_runtime",
        lambda result, client=None, model=None: calls.append(f"stage3:{model}") or agent_a_output,
    )
    monkeypatch.setattr(
        pipeline_automation,
        "write_agent_a_artifacts",
        lambda *args, **kwargs: calls.append("write_stage3"),
    )

    def fake_run_segment_prep(result, *, ffmpeg_bin, clips_dir, client=None):
        expected = runs_dir / "run_test" / pipeline_automation.SEGMENT_PREP_STAGE_DIR / "clips"
        assert clips_dir == expected
        assert ffmpeg_bin == "ffmpeg"
        assert client is fake_client
        calls.append("stage4")
        return segment_prep

    monkeypatch.setattr(pipeline_automation, "run_segment_prep", fake_run_segment_prep)
    monkeypatch.setattr(
        pipeline_automation,
        "write_segment_prep_artifacts",
        lambda *args, **kwargs: calls.append("write_stage4"),
    )
    async def fake_run_agent_b_all_cuts_parallel(*args, **kwargs):
        calls.append(f"stage5:{kwargs['model']}")
        return agent_b_result

    monkeypatch.setattr(
        pipeline_automation,
        "run_agent_b_all_cuts_parallel",
        fake_run_agent_b_all_cuts_parallel,
    )
    monkeypatch.setattr(
        pipeline_automation,
        "write_agent_b_artifacts",
        lambda *args, **kwargs: calls.append("write_stage5"),
    )
    monkeypatch.setattr(
        pipeline_automation,
        "run_agent_c",
        lambda *args, **kwargs: calls.append(f"stage6:{kwargs['flash_model']}") or agent_c_result,
    )
    monkeypatch.setattr(
        pipeline_automation,
        "write_agent_c_artifacts",
        lambda *args, **kwargs: calls.append("write_stage6"),
    )

    def fake_report(run_dir: Path) -> Path:
        report_path = run_dir / "pipeline_report.html"
        report_path.write_text("report", encoding="utf-8")
        calls.append("report")
        return report_path

    monkeypatch.setattr(pipeline_automation, "generate_pipeline_report", fake_report)

    result = pipeline_automation.run_pipeline_for_video(
        video_path,
        runs_dir=runs_dir,
        run_id="run_test",
        agent_a_model="agent-a-model",
        agent_b_model="agent-b-model",
        track_judge_model="track-judge-model",
    )

    assert result.status == "completed"
    assert result.run_dir == runs_dir / "run_test"
    assert result.report_path == runs_dir / "run_test" / "pipeline_report.html"
    assert calls == [
        "stage1:sample.mp4",
        "warnings",
        "write_stage1",
        "stage2:True",
        "write_stage2",
        "stage3:agent-a-model",
        "write_stage3",
        "stage4",
        "write_stage4",
        "stage5:agent-b-model",
        "write_stage5",
        "stage6:track-judge-model",
        "write_stage6",
        "report",
    ]


def test_run_pipeline_for_video_records_failure_and_returns_failed_result(tmp_path: Path, monkeypatch):
    video_path = tmp_path / "broken.mp4"
    video_path.write_text("video", encoding="utf-8")
    runs_dir = tmp_path / "runs"
    captured: dict[str, object] = {}

    monkeypatch.setattr(pipeline_automation, "create_gemini_client", lambda: object())

    def explode(path: Path):
        raise RuntimeError("stage 01 exploded")

    monkeypatch.setattr(pipeline_automation, "run_local_preprocessing", explode)

    def fake_failure_writer(*, stage, video_path, error, runs_dir, run_id, warnings=None):
        captured["stage"] = stage
        captured["video_path"] = video_path
        captured["message"] = error.message
        stage_dir = runs_dir / run_id / stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        return stage_dir

    monkeypatch.setattr(pipeline_automation, "write_stage_failure_artifacts", fake_failure_writer)

    def fake_report(run_dir: Path) -> Path:
        report_path = run_dir / "pipeline_report.html"
        report_path.write_text("partial report", encoding="utf-8")
        return report_path

    monkeypatch.setattr(pipeline_automation, "generate_pipeline_report", fake_report)

    result = pipeline_automation.run_pipeline_for_video(
        video_path,
        runs_dir=runs_dir,
        run_id="failed_run",
    )

    assert result.status == "failed"
    assert result.failed_stage == pipeline_automation.LOCAL_PREPROCESSING_STAGE_DIR
    assert result.error_message == "stage 01 exploded"
    assert result.report_path == runs_dir / "failed_run" / "pipeline_report.html"
    assert captured == {
        "stage": pipeline_automation.LOCAL_PREPROCESSING_STAGE_DIR,
        "video_path": str(video_path.resolve()),
        "message": "stage 01 exploded",
    }
