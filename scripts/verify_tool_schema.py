"""校验 AI 组件工具目录（ai/tools.json）的 schema 与元数据约定。

这是工具目录 schema 校验的唯一 Python 实现：MCP 服务启动、CI 和测试都复用这里的
``validate_tools`` / ``validate_file``，不再各自写一套断言。覆盖工具目录全部工具的结构、
``_meta.pro-ai-bridge/context``、``annotations.readOnlyHint`` 和写操作 ``confirm`` 约定。

严重度：
    FAIL  结构不合法 / 缺元数据 / name 重复（返回 1 的错误）
    WARN  写工具含 confirm 时约定不符（不阻断，但需记录）

用法：
    python scripts/verify_tool_schema.py               # 打印人读清单
    python scripts/verify_tool_schema.py --json        # 机器可读 JSON
    python scripts/verify_tool_schema.py --check       # 有 FAIL 时退出码 1

依赖：标准库 + jsonschema（系统 Python 3.12 已装；Pro Python 3.9 亦可运行）。
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
except ImportError as exc:  # pragma: no cover - 环境缺失时给出明确提示
    raise SystemExit("需要 jsonschema：pip install jsonschema") from exc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "ai" / "tools.json"
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
CONTEXTS = {"map", "project"}
CONTEXT_KEY = "pro-ai-bridge/context"
TARGET_KEY = "pro-ai-bridge/target"
CONFIRM_PROPERTY = "confirm"

FAIL = "FAIL"
WARN = "WARN"

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    ),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "null": lambda value: value is None,
}


@dataclass(frozen=True)
class Finding:
    """一条校验发现。code 是稳定契约，供测试和门禁按名称断言。"""

    severity: str
    code: str
    tool: str
    message: str

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "tool": self.tool,
            "message": self.message,
        }


@dataclass
class Report:
    """一次校验的结果。failures 非空即未通过。"""

    catalog: str
    total: int = 0
    failures: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def fail(self, code: str, tool: str, message: str) -> None:
        self.failures.append(Finding(FAIL, code, tool, message))

    def warn(self, code: str, tool: str, message: str) -> None:
        self.warnings.append(Finding(WARN, code, tool, message))

    def as_dict(self) -> dict:
        return {
            "catalog": self.catalog,
            "total": self.total,
            "ok": self.ok,
            "failure_count": len(self.failures),
            "warning_count": len(self.warnings),
            "failures": [item.as_dict() for item in self.failures],
            "warnings": [item.as_dict() for item in self.warnings],
        }


def load_catalog(path: Path | None = None) -> Any:
    """读取工具目录 JSON；兼容带 BOM 的文件。"""
    catalog_path = Path(path) if path is not None else DEFAULT_CATALOG
    with catalog_path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def _type_name(value: Any) -> str:
    return type(value).__name__


def _declared_type_matches(declared: Any, value: Any) -> bool:
    """检查 enum 元素是否符合 JSON Schema 的 type（支持 type 数组）。"""
    if isinstance(declared, list):
        return any(_declared_type_matches(item, value) for item in declared)
    check = _TYPE_CHECKS.get(declared)
    return True if check is None else check(value)


def _is_confirm_true_only(enum: Any) -> bool:
    """严格判断 enum 是否为 [true]；避免 Python 中 1 == True 的误判。"""
    return isinstance(enum, list) and len(enum) == 1 and enum[0] is True


def _validate_name(tool: dict, location: str, report: Report, seen: dict) -> None:
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        report.fail("name_missing", location, f"{location} 缺 name 或 name 为空")
        return
    if not NAME_PATTERN.fullmatch(name):
        report.fail("name_invalid", name, f"工具名 {name!r} 不匹配 ^[a-z][a-z0-9_]*$")
    seen.setdefault(name, []).append(location)


def _validate_description(tool: dict, label: str, report: Report) -> None:
    description = tool.get("description")
    if not isinstance(description, str) or not description.strip():
        report.fail("description_missing", label, f"{label} 缺非空 description")


def _validate_input_schema(schema: Any, label: str, report: Report) -> dict | None:
    """校验 inputSchema 本身；返回可继续检查的 schema（不可用时返回 None）。"""
    if not isinstance(schema, dict):
        report.fail("schema_missing", label, f"{label} 缺 inputSchema 或它不是对象")
        return None
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        detail = (exc.message or "").splitlines()[0]
        report.fail(
            "schema_invalid",
            label,
            f"{label} 的 inputSchema 不是合法 Draft 2020-12 JSON Schema：{detail}",
        )
        return None
    if schema.get("type") != "object":
        report.fail(
            "schema_not_object",
            label,
            f"{label} 的 inputSchema.type 必须是 object，实际 {schema.get('type')!r}",
        )
    return schema


def _validate_properties(
    schema: dict,
    label: str,
    report: Report,
    skip_enum_properties: frozenset = frozenset(),
) -> None:
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        # check_schema 已保证 properties 是对象；这里防御非 JSON 输入。
        return
    for field_name in schema.get("required") or []:
        if field_name not in properties:
            report.fail(
                "required_missing_property",
                label,
                f"{label} 的 required 字段 {field_name!r} 不在 properties 中",
            )
    for property_name, spec in properties.items():
        if property_name in skip_enum_properties:
            # 写工具的 confirm 由 _validate_confirm 按专用约定检查，只报 WARN 不报 FAIL。
            continue
        if not isinstance(spec, dict):
            continue
        if (
            "enum" not in spec
            or "type" not in spec
            or not isinstance(spec["enum"], list)
        ):
            continue
        mismatched = [
            value
            for value in spec["enum"]
            if not _declared_type_matches(spec["type"], value)
        ]
        if mismatched:
            report.fail(
                "enum_type_mismatch",
                label,
                f"{label} 的属性 {property_name!r} 有 enum 元素与 type={spec['type']!r} 不一致："
                + "、".join(repr(value) for value in mismatched),
            )


def _validate_confirm(schema: dict, label: str, report: Report) -> None:
    """写工具若暴露 confirm 参数，必须是 boolean 且只允许 true。"""
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict) or CONFIRM_PROPERTY not in properties:
        return
    confirm = properties[CONFIRM_PROPERTY]
    if not isinstance(confirm, dict):
        report.warn(
            "confirm_type_not_boolean",
            label,
            f"写工具 {label} 的 confirm 参数不是对象，无法约定 boolean/true",
        )
        return
    declared = confirm.get("type")
    if declared != "boolean":
        report.warn(
            "confirm_type_not_boolean",
            label,
            f"写工具 {label} 的 confirm.type 应为 boolean，实际 {declared!r}",
        )
    if not _is_confirm_true_only(confirm.get("enum")):
        report.warn(
            "confirm_enum_not_true",
            label,
            f"写工具 {label} 的 confirm.enum 应为 [true]，实际 {confirm.get('enum')!r}",
        )


def _validate_metadata(tool: dict, label: str, report: Report) -> bool | None:
    """校验 _meta/context 与 annotations/readOnlyHint；返回 readOnlyHint（不可用时 None）。"""
    meta = tool.get("_meta")
    if not isinstance(meta, dict):
        report.fail(
            "context_missing", label, f"{label} 缺 _meta 对象（需含 {CONTEXT_KEY}）"
        )
    else:
        context = meta.get(CONTEXT_KEY)
        if context is None:
            report.fail(
                "context_missing",
                label,
                f"{label} 缺 _meta[{CONTEXT_KEY!r}]（只允许 map/project）",
            )
        elif context not in CONTEXTS:
            report.fail(
                "context_invalid",
                label,
                f"{label} 的 _meta[{CONTEXT_KEY!r}] 必须是 map/project，实际 {context!r}",
            )

    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        report.fail(
            "readonly_missing",
            label,
            f"{label} 缺 annotations 对象（需含 readOnlyHint）",
        )
        return None
    readonly = annotations.get("readOnlyHint")
    if readonly is None:
        report.fail("readonly_missing", label, f"{label} 缺 annotations.readOnlyHint")
        return None
    if not isinstance(readonly, bool):
        report.fail(
            "readonly_not_bool",
            label,
            f"{label} 的 annotations.readOnlyHint 必须是 bool，实际 {readonly!r}",
        )
        return None
    return readonly


def validate_tools(tools: Any, catalog: str = "<memory>") -> Report:
    """校验工具目录列表，返回分级报告；不抛异常，便于测试直接构造坏数据。"""
    report = Report(catalog=catalog)
    if not isinstance(tools, list):
        report.fail(
            "catalog_not_array",
            "<catalog>",
            f"工具目录顶层必须是数组，实际 {_type_name(tools)}",
        )
        return report
    report.total = len(tools)
    if not tools:
        report.fail("catalog_empty", "<catalog>", "工具目录为空，至少需要一个工具")
        return report

    seen_names: dict = {}
    for index, tool in enumerate(tools):
        location = f"[{index}]"
        if not isinstance(tool, dict):
            report.fail(
                "tool_not_object",
                location,
                f"第 {index} 个工具不是对象，实际 {_type_name(tool)}",
            )
            continue
        name = tool.get("name")
        label = name if isinstance(name, str) and name else location
        _validate_name(tool, location, report, seen_names)
        _validate_description(tool, label, report)
        readonly = _validate_metadata(tool, label, report)
        schema = _validate_input_schema(tool.get("inputSchema"), label, report)
        if schema is not None:
            skip_enum = (
                frozenset({CONFIRM_PROPERTY}) if readonly is False else frozenset()
            )
            _validate_properties(schema, label, report, skip_enum_properties=skip_enum)
        if readonly is False and schema is not None:
            _validate_confirm(schema, label, report)

    for name, locations in seen_names.items():
        if len(locations) > 1:
            report.fail(
                "name_duplicate",
                name,
                f"工具名重复：{name}（出现 {len(locations)} 次，索引 {locations}）",
            )
    return report


def validate_file(path: Path | None = None) -> Report:
    """读取并校验工具目录文件；读取失败也转成 FAIL 报告。"""
    catalog_path = Path(path) if path is not None else DEFAULT_CATALOG
    try:
        tools = load_catalog(catalog_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report = Report(catalog=str(catalog_path))
        report.fail("catalog_unreadable", "<catalog>", f"读不到工具目录：{exc}")
        return report
    return validate_tools(tools, catalog=str(catalog_path))


def render_text(report: Report) -> str:
    lines = [f"工具目录：{report.catalog}（{report.total} 个工具）"]
    for item in report.failures:
        lines.append(f"FAIL [{item.code}] {item.tool}：{item.message}")
    for item in report.warnings:
        lines.append(f"WARN [{item.code}] {item.tool}：{item.message}")
    if not report.failures and not report.warnings:
        lines.append("全部检查通过：结构、元数据与 confirm 约定无问题。")
    lines.append(
        "结论：{}（工具 {}，FAIL {}，WARN {}）".format(
            "PASS" if report.ok else "FAIL",
            report.total,
            len(report.failures),
            len(report.warnings),
        )
    )
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="校验 ai/tools.json 的工具 schema 与元数据"
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="有 FAIL 时返回退出码 1（WARN 不阻断）",
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help=f"工具目录路径（默认 {DEFAULT_CATALOG}）",
    )
    args = parser.parse_args(argv)

    report = validate_file(Path(args.catalog) if args.catalog else None)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 1 if args.check and report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
