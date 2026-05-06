from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

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
        lambda result, client=None, ffmpeg_bin=None: calls.append(
            f"stage2:{client is fake_client}:{ffmpeg_bin}"
        ) or full_video_asset,
    )
    monkeypatch.setattr(
        pipeline_automation,
        "write_full_video_asset_artifacts",
        lambda *args, **kwargs: calls.append("write_stage2"),
    )
    def fake_run_agent_a_runtime(
        result,
        *,
        client=None,
        model=None,
        temperature=None,
        generation_params=None,
        video_fps=None,
    ):
        _ = temperature
        calls.append(
            f"stage3:{model}:{generation_params.temperature}:{generation_params.top_p}:{video_fps}"
        )
        return agent_a_output

    monkeypatch.setattr(pipeline_automation, "run_agent_a_runtime", fake_run_agent_a_runtime)
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
        generation_params = kwargs["generation_params"]
        calls.append(
            f"stage5:{kwargs['model']}:{generation_params.temperature}:"
            f"{generation_params.top_p}:{kwargs['video_fps']}"
        )
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
        lambda *args, **kwargs: calls.append(
            "stage6:"
            f"{kwargs['flash_model']}:"
            f"{kwargs['track_judge_generation_params'].temperature}:"
            f"{kwargs['track_judge_generation_params'].top_p}"
        ) or agent_c_result,
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
        top_p=0.8,
        agent_a_temperature=0.0,
        agent_b_temperature=0.0,
        track_judge_temperature=0.0,
    )

    assert result.status == "completed"
    assert result.run_dir == runs_dir / "run_test"
    assert result.report_path == runs_dir / "run_test" / "pipeline_report.html"
    assert calls == [
        "stage1:sample.mp4",
        "warnings",
        "write_stage1",
        "stage2:True:ffmpeg",
        "write_stage2",
        "stage3:agent-a-model:0.0:0.8:5.0",
        "write_stage3",
        "stage4",
        "write_stage4",
        "stage5:agent-b-model:0.0:0.8:5.0",
        "write_stage5",
        "stage6:track-judge-model:0.0:0.8",
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


def test_run_pipeline_for_video_applies_shared_model_and_generation_params(
    tmp_path: Path,
    monkeypatch,
):
    video_path = tmp_path / "sample.mp4"
    video_path.write_text("video", encoding="utf-8")
    runs_dir = tmp_path / "runs"
    captured: dict[str, object] = {}

    fake_client = object()
    monkeypatch.setattr(pipeline_automation, "create_gemini_client", lambda: fake_client)
    monkeypatch.setattr(pipeline_automation, "run_local_preprocessing", lambda path: object())
    monkeypatch.setattr(pipeline_automation, "collect_local_preprocessing_warnings", lambda result: [])
    monkeypatch.setattr(pipeline_automation, "write_local_preprocessing_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_automation, "prepare_full_video_asset", lambda *args, **kwargs: object())
    monkeypatch.setattr(pipeline_automation, "write_full_video_asset_artifacts", lambda *args, **kwargs: None)

    def fake_run_agent_a_runtime(result, *, client=None, model=None, generation_params=None, **kwargs):
        captured["agent_a"] = (
            model,
            generation_params.temperature,
            generation_params.seed,
            kwargs["video_fps"],
        )
        return SimpleNamespace(response=SimpleNamespace(entity_registry="registry"))

    monkeypatch.setattr(pipeline_automation, "run_agent_a_runtime", fake_run_agent_a_runtime)
    monkeypatch.setattr(pipeline_automation, "write_agent_a_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_automation, "run_segment_prep", lambda *args, **kwargs: object())
    monkeypatch.setattr(pipeline_automation, "write_segment_prep_artifacts", lambda *args, **kwargs: None)

    async def fake_run_agent_b_all_cuts_parallel(*args, **kwargs):
        params = kwargs["generation_params"]
        captured["agent_b"] = (
            kwargs["model"],
            params.temperature,
            params.seed,
            kwargs["video_fps"],
        )
        return object()

    monkeypatch.setattr(pipeline_automation, "run_agent_b_all_cuts_parallel", fake_run_agent_b_all_cuts_parallel)
    monkeypatch.setattr(pipeline_automation, "write_agent_b_artifacts", lambda *args, **kwargs: None)

    def fake_run_agent_c(*args, **kwargs):
        params = kwargs["track_judge_generation_params"]
        captured["track_judge"] = (kwargs["flash_model"], params.temperature, params.seed)
        return object()

    monkeypatch.setattr(pipeline_automation, "run_agent_c", fake_run_agent_c)
    monkeypatch.setattr(pipeline_automation, "write_agent_c_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pipeline_automation,
        "generate_pipeline_report",
        lambda run_dir: run_dir / "pipeline_report.html",
    )

    result = pipeline_automation.run_pipeline_for_video(
        video_path,
        runs_dir=runs_dir,
        run_id="shared_params",
        model="gemini-3.1-pro-preview",
        temperature=0.2,
        seed=42,
        video_fps=5.0,
        agent_b_model="gemini-2.5-pro",
        track_judge_temperature=0.0,
    )

    assert result.status == "completed"
    assert captured == {
        "agent_a": ("gemini-3.1-pro-preview", 0.2, 42, 5.0),
        "agent_b": ("gemini-2.5-pro", 0.2, 42, 5.0),
        "track_judge": ("gemini-3.1-pro-preview", 0.0, 42),
    }


def test_run_pipeline_for_video_uses_default_temperature_and_seed_when_omitted(
    tmp_path: Path,
    monkeypatch,
):
    video_path = tmp_path / "sample.mp4"
    video_path.write_text("video", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(pipeline_automation, "create_gemini_client", lambda: object())
    monkeypatch.setattr(pipeline_automation, "run_local_preprocessing", lambda path: object())
    monkeypatch.setattr(pipeline_automation, "collect_local_preprocessing_warnings", lambda result: [])
    monkeypatch.setattr(pipeline_automation, "write_local_preprocessing_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_automation, "prepare_full_video_asset", lambda *args, **kwargs: object())
    monkeypatch.setattr(pipeline_automation, "write_full_video_asset_artifacts", lambda *args, **kwargs: None)

    def fake_run_agent_a_runtime(result, *, generation_params=None, **kwargs):
        captured["agent_a"] = (generation_params.temperature, generation_params.seed)
        return SimpleNamespace(response=SimpleNamespace(entity_registry="registry"))

    monkeypatch.setattr(pipeline_automation, "run_agent_a_runtime", fake_run_agent_a_runtime)
    monkeypatch.setattr(pipeline_automation, "write_agent_a_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_automation, "run_segment_prep", lambda *args, **kwargs: object())
    monkeypatch.setattr(pipeline_automation, "write_segment_prep_artifacts", lambda *args, **kwargs: None)

    async def fake_run_agent_b_all_cuts_parallel(*args, **kwargs):
        params = kwargs["generation_params"]
        captured["agent_b"] = (params.temperature, params.seed)
        return object()

    monkeypatch.setattr(pipeline_automation, "run_agent_b_all_cuts_parallel", fake_run_agent_b_all_cuts_parallel)
    monkeypatch.setattr(pipeline_automation, "write_agent_b_artifacts", lambda *args, **kwargs: None)

    def fake_run_agent_c(*args, **kwargs):
        params = kwargs["track_judge_generation_params"]
        captured["track_judge"] = (params.temperature, params.seed)
        return object()

    monkeypatch.setattr(pipeline_automation, "run_agent_c", fake_run_agent_c)
    monkeypatch.setattr(pipeline_automation, "write_agent_c_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pipeline_automation,
        "generate_pipeline_report",
        lambda run_dir: run_dir / "pipeline_report.html",
    )

    result = pipeline_automation.run_pipeline_for_video(
        video_path,
        runs_dir=tmp_path / "runs",
        run_id="defaults",
    )

    assert result.status == "completed"
    assert captured == {
        "agent_a": (
            pipeline_automation.DEFAULT_AUTOMATION_TEMPERATURE,
            pipeline_automation.DEFAULT_AUTOMATION_SEED,
        ),
        "agent_b": (
            pipeline_automation.DEFAULT_AUTOMATION_TEMPERATURE,
            pipeline_automation.DEFAULT_AUTOMATION_SEED,
        ),
        "track_judge": (
            pipeline_automation.DEFAULT_AUTOMATION_TEMPERATURE,
            pipeline_automation.DEFAULT_AUTOMATION_SEED,
        ),
    }


def test_run_pipeline_for_inputs_supports_batch_video_concurrency(tmp_path: Path, monkeypatch):
    video_a = tmp_path / "a.mp4"
    video_b = tmp_path / "b.mp4"
    video_a.write_text("a", encoding="utf-8")
    video_b.write_text("b", encoding="utf-8")
    seen: list[str] = []

    def fake_run_pipeline_for_video(video_path: Path, **kwargs):
        seen.append(f"{video_path.name}:{kwargs['ffmpeg_bin']}:{kwargs['max_agent_b_concurrency']}")
        return pipeline_automation.PipelineAutomationResult(
            video_path=video_path,
            run_id=video_path.stem,
            run_dir=tmp_path / "runs" / video_path.stem,
            status="completed",
        )

    monkeypatch.setattr(
        pipeline_automation,
        "run_pipeline_for_video",
        fake_run_pipeline_for_video,
    )

    results = pipeline_automation.run_pipeline_for_inputs(
        [video_a, video_b],
        runs_dir=tmp_path / "runs",
        ffmpeg_bin="custom-ffmpeg",
        max_video_concurrency=2,
        max_agent_b_concurrency=7,
    )

    assert [result.video_path.name for result in results] == ["a.mp4", "b.mp4"]
    assert sorted(seen) == [
        "a.mp4:custom-ffmpeg:7",
        "b.mp4:custom-ffmpeg:7",
    ]


def test_run_pipeline_for_inputs_rejects_invalid_batch_video_concurrency(tmp_path: Path):
    video_path = tmp_path / "sample.mp4"
    video_path.write_text("video", encoding="utf-8")

    with pytest.raises(ValueError, match="max_video_concurrency must be >= 1"):
        pipeline_automation.run_pipeline_for_inputs(
            [video_path],
            max_video_concurrency=0,
        )
