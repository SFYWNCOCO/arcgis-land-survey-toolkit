"""Private subprocess entry point for one bounded Sentinel COG warp."""
from __future__ import annotations

import json
import os
import sys

from acceptance.sentinel_prepare import (
    HTTP_CONNECT_TIMEOUT_SECONDS,
    HTTP_MAX_RETRIES,
    HTTP_MULTIRANGE_MODE,
    HTTP_RETRY_DELAY_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    _temporary_gdal_config,
    _warp_role,
)


def _atomic_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main(task_path):
    with open(task_path, "r", encoding="utf-8") as stream:
        task = json.load(stream)
    try:
        from osgeo import gdal
        gdal.UseExceptions()
        config = {
            "GDAL_HTTP_CONNECTTIMEOUT": HTTP_CONNECT_TIMEOUT_SECONDS,
            "GDAL_HTTP_TIMEOUT": HTTP_TIMEOUT_SECONDS,
            "GDAL_HTTP_MAX_RETRY": HTTP_MAX_RETRIES,
            "GDAL_HTTP_RETRY_DELAY": HTTP_RETRY_DELAY_SECONDS,
            "GDAL_HTTP_MULTIRANGE": HTTP_MULTIRANGE_MODE,
        }

        def progress(role, percent):
            _atomic_json(task["progress_path"], {"role": role, "percent": percent})

        with _temporary_gdal_config(gdal, config):
            result = _warp_role(
                gdal, task["group"], task["role"], task["output_path"],
                task["cutline_path"], task["target_wkt"], task["resolution"],
                task["resampling"], timeout_seconds=task["timeout_seconds"],
                progress=progress,
            )
        _atomic_json(task["result_path"], result)
        return 0
    except BaseException as exc:
        try:
            _atomic_json(task["error_path"], {"type": type(exc).__name__, "message": str(exc)})
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
