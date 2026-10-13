from pathlib import Path

import pytest

from anna_backend.execution_log import logged_run, operation


@pytest.mark.asyncio
async def test_failed_run_preserves_utf8_traceback_and_separate_run_logs(tmp_path: Path):
    expected = RuntimeError("Помилка української генерації")

    class Pipeline:
        @logged_run
        async def run(self, *, tmp_dir, fail):
            with operation("transcriber.transcribe strategy=word"):
                if fail:
                    raise expected
            return "done"

    pipeline = Pipeline()
    with pytest.raises(RuntimeError) as caught:
        await pipeline.run(tmp_dir=tmp_path / "tmp", fail=True)
    assert caught.value is expected
    failed_path = pipeline.last_log_path
    report = failed_path.read_text(encoding="utf-8")
    assert "START transcriber.transcribe strategy=word" in report
    assert "FAIL transcriber.transcribe strategy=word elapsed=" in report
    assert "Traceback (most recent call last)" in report
    assert "RuntimeError: Помилка української генерації" in report
    assert "OK transcriber.transcribe" not in report
    assert await pipeline.run(tmp_dir=tmp_path / "tmp", fail=False) == "done"
    assert pipeline.last_log_path != failed_path
    assert failed_path.read_text(encoding="utf-8") == report
    assert "OK transcriber.transcribe" in pipeline.last_log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_pipeline_reuses_outer_cli_log(tmp_path):
    from anna_backend.execution_log import execution_session

    class Pipeline:
        @logged_run
        async def run(self, *, tmp_dir):
            return "done"

    pipeline = Pipeline()
    with execution_session(tmp_path / "logs") as path:
        assert await pipeline.run(tmp_dir=tmp_path / "tmp") == "done"
        assert pipeline.last_log_path == path
    assert len(list((tmp_path / "logs").glob("*.log"))) == 1
    assert "OK " in path.read_text(encoding="utf-8")
