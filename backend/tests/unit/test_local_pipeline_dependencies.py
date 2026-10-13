from __future__ import annotations

import ast
from pathlib import Path


def test_local_pipeline_has_no_concrete_media_dependencies_or_ffmpeg_arguments() -> None:
    source_path = (
        Path(__file__).parents[2] / "src" / "anna_backend" / "local_pipeline.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert imported_modules.isdisjoint(
        {
            "subprocess",
            "anna_backend.audio.learning",
            "anna_backend.subtitles.ass",
            "anna_backend.video.ffmpeg",
        }
    )

    pipeline_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LocalPipeline"
    )
    run_method = next(
        node
        for node in pipeline_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run"
    )
    argument_names = {
        argument.arg
        for argument in (
            *run_method.args.posonlyargs,
            *run_method.args.args,
            *run_method.args.kwonlyargs,
        )
    }
    assert argument_names.isdisjoint({"ffmpeg_path", "ffprobe_path"})
