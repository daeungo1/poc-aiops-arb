"""Helper target for spawn-based coverage publication tests."""

from __future__ import annotations

import json
from pathlib import Path

import enterprise.coverage as coverage_module
from enterprise.coverage import write_coverage_reports


PROCESS_START_TIMEOUT_SECONDS = 30


def _publish_in_process(
    report,
    reports_dir,
    result_path,
    staged_event=None,
    release_event=None,
    done_event=None,
    lock_attempted_event=None,
):
    if staged_event is not None and release_event is not None:
        original_stage_content = coverage_module._stage_content

        def blocking_stage_content(path, content):
            staged_path = original_stage_content(path, content)
            staged_event.set()
            if not release_event.wait(timeout=PROCESS_START_TIMEOUT_SECONDS):
                raise TimeoutError("test publisher was not released")
            return staged_path

        coverage_module._stage_content = blocking_stage_content
    if lock_attempted_event is not None:
        original_file_lock = coverage_module.FileLock

        class SignalingFileLock(original_file_lock):
            def __enter__(self):
                lock_attempted_event.set()
                return super().__enter__()

        coverage_module.FileLock = SignalingFileLock
    try:
        bundle = write_coverage_reports(report, reports_dir, lock_timeout_seconds=5)
        Path(result_path).write_text(
            json.dumps({"generation_id": bundle.generation_id, "status": "ok"}),
            encoding="utf-8",
        )
    except BaseException as exc:
        Path(result_path).write_text(
            json.dumps(
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "status": "error",
                }
            ),
            encoding="utf-8",
        )
    finally:
        if done_event is not None:
            done_event.set()