"""一键采样任务的原子状态、断点收据和阶段间取消。"""
from __future__ import annotations

import hashlib
import copy
import json
import os
import tempfile
import time
import uuid


STATE_NAME = "task_state.json"
CANCEL_NAME = "cancel.request"
PHASES = ("preflight", "data_prepared", "sampling_prepared", "forms_prepared", "imagery_prepared",
          "maps_built", "maps_exported", "completed")


class TaskCancelled(RuntimeError):
    pass


def _process_identity(pid):
    """只查询进程，不向 Windows 进程发送控制事件；创建时间用于识别复用 PID。"""
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
            return True, None
        except ProcessLookupError:
            return False, None
        except PermissionError:
            return True, None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID 不存在
            return False, None
        if error == 5:   # 权限不足时不能误认为已退出
            return True, None
        raise OSError(error, "无法核实任务进程状态")
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        times = [wintypes.FILETIME() for _ in range(4)]
        created = None
        if kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        return code.value == 259, created  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def _atomic_json(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".task-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _fingerprint_path(path):
    path = os.path.abspath(os.fspath(path))
    dataset_path = path
    if not os.path.exists(path) and ".gdb" in path.lower():
        end = path.lower().index(".gdb") + 4
        path = path[:end]
    entries = []
    if os.path.isfile(path):
        if path.lower().endswith(".shp"):
            stem = os.path.splitext(path)[0]
            candidates = [stem + suffix for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".shp.xml")
                          if os.path.isfile(stem + suffix)]
        else:
            candidates = [path]
    elif os.path.isdir(path):
        candidates = []
        for root, _dirs, files in os.walk(path):
            for name in files:
                if name.lower().endswith(".lock"):
                    continue
                candidates.append(os.path.join(root, name))
                if len(candidates) > 20000:
                    raise ValueError("输入目录文件过多，无法可靠生成任务指纹：{}".format(path))
    else:
        return {"path": dataset_path, "exists": False}
    for filename in sorted(candidates):
        stat = os.stat(filename)
        content_hash = hashlib.sha256()
        with open(filename, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content_hash.update(chunk)
        entries.append((os.path.relpath(filename, path) if os.path.isdir(path) else os.path.basename(filename),
                        stat.st_size, content_hash.hexdigest()))
    digest = hashlib.sha256(json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return {"path": dataset_path, "exists": True, "files": len(entries), "sha256": digest.hexdigest()}


def input_fingerprint(source, options):
    safe_options = copy.deepcopy(options or {})
    for key in ("tianditu_key", "token", "password"):
        if safe_options.get(key):
            safe_options[key] = {"credential_sha256": hashlib.sha256(str(safe_options[key]).encode("utf-8")).hexdigest()}
    safe_options = json.loads(json.dumps(safe_options, ensure_ascii=False, default=str))
    attachments = {key: _fingerprint_path(value) for key, value in (options or {}).items()
                   if key in ("imagery_raster", "template") and value}
    raster = (options or {}).get("imagery_raster")
    provider = (options or {}).get('imagery_provider')
    if provider and os.path.isfile(str(provider)):
        attachments['online_service_config'] = _fingerprint_path(provider)
    if raster and os.path.isfile(str(raster) + ".source.json"):
        attachments["imagery_metadata"] = _fingerprint_path(str(raster) + ".source.json")
    for role, choice in ((options or {}).get("factor_sources") or {}).items():
        attachments["factor:" + role] = _fingerprint_path(choice["path"])
    for uid, choice in ((options or {}).get("unit_imagery_sources") or {}).items():
        if choice.get('provider') and os.path.isfile(str(choice['provider'])):
            attachments['unit_imagery_service:' + uid] = _fingerprint_path(choice['provider'])
        if choice.get("raster"):
            attachments["unit_imagery:" + uid] = _fingerprint_path(choice["raster"])
            if os.path.isfile(str(choice["raster"]) + ".source.json"):
                attachments["unit_imagery_metadata:" + uid] = _fingerprint_path(str(choice["raster"]) + ".source.json")
    root = os.path.dirname(os.path.abspath(__file__))
    engine_files = {
        "data_prepared": ("workflow.py", "oneclick.py", "unit_builder.py", "task_state.py", "profile.py", "factor_overlay.py", "data_intake.py", "unit_review.py", "existing_unit_qa.py"),
        "sampling_prepared": ("grid_sampling.py", "sampling_metrics.py", "sampling_qa.py", "sampling.py", "rules.py", "bulk_design.py", "point_adjustment.py"),
        "forms_prepared": ("forms.py", "../config/region_counties.json", "../templates/表单_附录D_实地踏勘表.docx",
                           "../templates/表单_附录E_土壤样品采集记录表.docx", "../templates/表单_附录F_补充耕地质量鉴定意见表.docx",
                           "../templates/表单_样品标签.docx"),
        "imagery_prepared": ("imagery.py", "imagery_qa.py", "sheet_plan.py", "map_layout_style.py"),
        "maps_built": ("map_worker.py", "sheet_plan.py", "../templates/补充耕地鉴定图_A4.pagx"),
        "maps_exported": ("map_export.py", "map_validation.py"),
    }
    engine = {phase: {name: _fingerprint_path(os.path.join(root, name)).get("sha256") for name in names}
              for phase, names in engine_files.items()}
    payload = {"source": _fingerprint_path(source), "options": safe_options,
               "attachments": attachments, "engine": engine}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), payload


def read_task(out_dir):
    path = os.path.join(os.path.abspath(out_dir), STATE_NAME)
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def invalidated_phase(prior_details, current_details):
    if prior_details.get("source") != current_details.get("source"):
        return "data_prepared"
    old, new = prior_details.get("options", {}), current_details.get("options", {})
    changed = {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
    affected = []
    before_rules = (old.get('meta') or {}).get('project_rules') or {}
    after_rules = (new.get('meta') or {}).get('project_rules') or {}
    changed_rules = {key for key in set(before_rules) | set(after_rules) if before_rules.get(key) != after_rules.get(key)}
    before_meta, after_meta = old.get('meta') or {}, new.get('meta') or {}
    form_meta = {'project_name', '项目名称', 'city', '市', '市（州）', 'county', '县', '县（市、区）',
                 'town', '乡', '乡（镇、街办）', 'village', '村'}
    if any(before_meta.get(key) != after_meta.get(key) for key in form_meta):
        affected.append('forms_prepared')
    if changed_rules & {'excluded_parcels'}:
        affected.append('data_prepared')
    if changed_rules & {'point_counts_by_unit'}:
        affected.append('sampling_prepared')
    if changed_rules & {'combine_parcels', 'index_scale', 'sample_min_scale', 'map_scales'}:
        affected.append('imagery_prepared')
    if changed & {"id_field", "unit_field", "factor_fields", "factor_sources", "project_number", "sampling_groups", "max_mu", "gap_m", "allow_multipart", "selection_oids", "source_where_clause"} or any(
        prior_details.get("attachments", {}).get(key) != current_details.get("attachments", {}).get(key)
        for key in set(prior_details.get("attachments", {})) | set(current_details.get("attachments", {})) if key.startswith("factor:")):
        affected.append("data_prepared")
    if changed & {"cell_size_m", "min_points", "min_spacing_m", "inset_m"}:
        affected.append("sampling_prepared")
    if changed & {"imagery_raster", "imagery_provider", "tianditu_key", "unit_imagery_sources"} or any(
            prior_details.get("attachments", {}).get(key) != current_details.get("attachments", {}).get(key)
            for key in set(prior_details.get("attachments", {})) | set(current_details.get("attachments", {})) if key.startswith("unit_imagery")) or \
            any(prior_details.get("attachments", {}).get(key) != current_details.get("attachments", {}).get(key)
                for key in ("imagery_raster", "imagery_metadata", "online_service_config")):
        affected.append("imagery_prepared")
    if changed or prior_details.get("attachments") != current_details.get("attachments"):
        affected.append("maps_built")
    for phase in PHASES[1:-1]:
        if prior_details.get("engine", {}).get(phase) != current_details.get("engine", {}).get(phase):
            affected.append(phase)
    return min(affected, key=PHASES.index) if affected else None


def begin_task(out_dir, source, options, action="run"):
    root = os.path.abspath(os.fspath(out_dir))
    os.makedirs(root, exist_ok=True)
    state_path = os.path.join(root, STATE_NAME)
    digest, fingerprint = input_fingerprint(source, options)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if action == "run":
        if os.listdir(root):
            raise FileExistsError("执行新任务要求选择空文件夹；已有任务请使用“继续”。")
        attempt = 1
        run_dir = root
        prior = None
    elif action == "resume":
        if not os.path.isfile(state_path):
            raise FileNotFoundError("没有可继续的任务状态文件：{}".format(state_path))
        prior = read_task(root)
        if prior.get("status") == "RUNNING":
            owner_pid = prior.get("pid")
            if owner_pid:
                alive, created = _process_identity(owner_pid)
                expected = prior.get("process_created")
                if alive and (not expected or not created or expected == created):
                    raise RuntimeError("该任务仍由进程 {} 执行，请等待或先取消。".format(owner_pid))
        changed = prior.get("input_fingerprint") != digest
        attempt = int(prior.get("attempt", 1)) + 1
        run_dir = prior.get("latest_run_dir") or root
    else:
        raise ValueError("未知任务动作：{}".format(action))

    changed = bool(prior and prior.get("input_fingerprint") != digest)
    invalidate = invalidated_phase((prior or {}).get("input_details", {}), fingerprint) if changed else None
    phases = copy.deepcopy((prior or {}).get("phases")) or {phase: {"status": "PENDING"} for phase in PHASES}
    if invalidate:
        for phase in PHASES[PHASES.index(invalidate):]:
            phases[phase] = {"status": "PENDING", "invalidated": True}
        if invalidate == "data_prepared":
            run_dir = os.path.join(root, "attempt_{:02d}".format(attempt))
            while os.path.exists(run_dir):
                attempt += 1
                run_dir = os.path.join(root, "attempt_{:02d}".format(attempt))
            os.makedirs(run_dir)
    state = {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex,
        "status": "RUNNING",
        "action": action,
        "attempt": attempt,
        "created_at": (prior or {}).get("created_at", now),
        "updated_at": now,
        "input_fingerprint": digest,
        "input_details": fingerprint,
        "input_changed_on_resume": changed,
        "current_phase": "preflight",
        "phases": phases,
        "invalidated_from": invalidate,
        "latest_run_dir": run_dir,
        "state_root": root,
        "pid": os.getpid(),
        "design_revision": int((prior or {}).get("design_revision", 0)),
        "process_created": _process_identity(os.getpid())[1],
        "report_path": None,
        "history": list((prior or {}).get("history", [])),
    }
    if changed:
        state["history"].append({"time": now, "event": "输入或参数改变，相关下游阶段失效", "from_phase": invalidate})
    cancel_path = os.path.join(root, CANCEL_NAME)
    if os.path.exists(cancel_path):
        os.remove(cancel_path)
    # Publish only after removing the previous attempt's marker. Otherwise a
    # controller can observe RUNNING, request stop, and have that request erased.
    _atomic_json(state_path, state)
    return state


def start_phase(out_dir, state, phase):
    if phase not in PHASES:
        raise ValueError("未知任务阶段：{}".format(phase))
    state["current_phase"] = phase
    state["phases"][phase] = {"status": "RUNNING", "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    _atomic_json(os.path.join(os.path.abspath(out_dir), STATE_NAME), state)


def record_phase(out_dir, state, phase, detail=None):
    if state.get('status') != 'RUNNING':
        return
    if phase not in PHASES:
        raise ValueError("未知任务阶段：{}".format(phase))
    state["current_phase"] = phase
    state["phases"][phase] = {"status": "COMPLETED", "detail": detail,
                              "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_json(os.path.join(os.path.abspath(out_dir), STATE_NAME), state)
    check_cancel(out_dir, state)


def check_cancel(out_dir, state):
    cancel_path = os.path.join(os.path.abspath(out_dir), CANCEL_NAME)
    if os.path.isfile(cancel_path):
        with open(cancel_path, encoding='utf-8') as stream:
            requested_run = stream.read().strip()
        if requested_run != state['run_id']:
            os.remove(cancel_path)
            return
        state["status"] = "CANCELLED"
        state["history"].append({"time": state["updated_at"], "event": "用户请求在阶段边界取消任务"})
        _atomic_json(os.path.join(os.path.abspath(out_dir), STATE_NAME), state)
        os.remove(cancel_path)
        raise TaskCancelled("任务已取消；已完成阶段和输出保留在磁盘，可选择继续。")


def request_cancel(out_dir):
    root = os.path.abspath(os.fspath(out_dir))
    state_path = os.path.join(root, STATE_NAME)
    if not os.path.isfile(state_path):
        raise FileNotFoundError("没有正在执行的任务状态文件：{}".format(state_path))
    state = read_task(root)
    if state.get("status") != "RUNNING":
        return {"status": "NOT_RUNNING", "state_path": state_path}
    fd, temporary = tempfile.mkstemp(prefix='.cancel-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(state['run_id'])
        os.replace(temporary, os.path.join(root, CANCEL_NAME))
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    # The worker is the sole JSON writer. The marker is safe to create from another process.
    return {"status": "CANCEL_REQUESTED", "state_path": state_path}


def finish_task(out_dir, state, status, report_path=None, error=None):
    if state.get("status") != "RUNNING":
        return state
    if status != 'FAILED':
        check_cancel(out_dir, state)
    state["status"] = status
    state["report_path"] = report_path
    state["error"] = str(error) if error else None
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_json(os.path.join(os.path.abspath(out_dir), STATE_NAME), state)
    return state
