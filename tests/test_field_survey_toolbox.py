import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_field_survey_bundle_inspector_is_registered_as_a_toolbox_tool():
    toolbox_path = ROOT / "toolboxes" / "SupplementaryCultivatedLandAcceptance.pyt"
    tree = ast.parse(toolbox_path.read_text(encoding="utf-8"))
    toolbox_class = next(node for node in tree.body
                         if isinstance(node, ast.ClassDef) and node.name == "Toolbox")
    init = next(node for node in toolbox_class.body
                if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    registered = set()
    for node in ast.walk(init):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "tools":
                    if isinstance(node.value, ast.List):
                        registered.update(item.id for item in node.value.elts if isinstance(item, ast.Name))

    assert "InspectFieldSurveyBundle" in registered
    tool = next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == "InspectFieldSurveyBundle")
    assert any(isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "description" for target in node.targets)
               for node in tool.body)
