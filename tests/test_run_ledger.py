"""运行账本与工具面的离线契约测试（纯标准库，系统 Python 可跑）。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT, ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from acceptance import run_ledger


def test_build_entry_keeps_only_observable_fields():
    report = {
        "技术状态": "PASS",
        "业务审核状态": "NEEDS_REVIEW",
        "评价单元数": 5,
        "图斑数": 6,
        "网格": {"设计点": 9},
        "任务运行ID": "run-1",
        "输入指纹": "abc",
        "成果报告路径": str(Path("C:/data/项目_成果") / "一键成果报告.json"),
        "设计确认": {"operator": "张三"},
    }
    entry = run_ledger.build_entry(report, action="preview", elapsed_s=12.34)
    assert entry["status"] == "PASS"
    assert entry["units"] == 5 and entry["parcels"] == 6 and entry["points"] == 9
    assert entry["elapsed_s"] == 12.3
    assert entry["manual_confirmed"] is True
    assert entry["task_dir"] == "项目_成果"
    assert "检查清单" not in entry


def test_build_entry_on_failure_records_error_only():
    entry = run_ledger.build_entry(None, action="preview", elapsed_s=1.0,
                                   status="FAILED", error="影像服务需要 key")
    assert entry["status"] == "FAILED"
    assert entry["units"] is None
    assert "key" in entry["error"]


def test_record_writes_jsonl_and_summarize_counts(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    report = {"技术状态": "PASS", "评价单元数": 2, "图斑数": 2, "网格": {"设计点": 6}}
    path = run_ledger.record(report, action="check", elapsed_s=0.2, root=ledger)
    assert path == ledger and ledger.is_file()
    run_ledger.record(None, action="check", elapsed_s=0.1, status="FAILED",
                      error="boom", root=ledger)
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 2 and rows[1]["status"] == "FAILED"
    summary = run_ledger.summarize(ledger)
    assert summary["任务数"] == 2
    assert summary["按动作"]["check"]["任务数"] == 2
    assert summary["按动作"]["check"]["失败"] == 1


def test_record_never_raises_on_bad_root(tmp_path):
    bad = tmp_path / "file-not-dir"
    bad.write_text("x", encoding="utf-8")
    assert run_ledger.record({}, action="check", elapsed_s=0, root=bad / "ledger.jsonl") is None
