from pathlib import Path
import ast
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "addin" / "AcceptanceAddin" / "Config.daml"
COMMAND = ROOT / "addin" / "AcceptanceAddin" / "Commands" / "BulkDensityQAButton.cs"
TOOLBOX = ROOT / "toolboxes" / "SupplementaryCultivatedLandAcceptance.pyt"
COMMANDS = ROOT / "addin" / "AcceptanceAddin" / "Commands"


def test_wizard_visual_review_uses_real_image_client_and_refreshes_the_existing_report():
    pane = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    assert 'AI检查整套图件' in pane
    assert 'ReviewMapAsync(settings, choice.Path, choice.ReviewContext' in pane
    assert 'receipt["render_key"] = choice.RenderKey' in pane
    assert '".visual-review.json"' in pane
    assert 'GetToolPath("RefreshMapVisualReviews", "GridSampling.pyt")' in pane
    assert 'await ReviewMapPreviewsAsync(taskRoot, automatic: true)' in pane
    manual = pane.split('_reviewMaps.Click +=', 1)[1].split('_mapPreviewChoices.SelectionChanged', 1)[0]
    assert '_previewTaskRoot ?? (string)_receipt?["root"]' in manual
    assert 'SameTask(reviewRoot, _outDir.Text)' in manual
    assert 'await ReviewMapPreviewsAsync(reviewRoot, automatic: false)' in manual
    assert '_visualReviewCancellation.Cancel()' in pane
    assert 'ImageHash(choice.Path) != before' in pane
    assert '业务审核仍待确认' in pane
    assert 'BuildMapReviewContext(output, report)' in pane
    assert '"presentation", "map_date"' in pane


def test_bulk_density_spatial_qa_has_a_ribbon_entry_and_opens_the_gp_tool():
    ns = {"daml": "http://schemas.esri.com/DADF/Registry"}
    root = ET.parse(CONFIG).getroot()
    button = root.find(".//daml:controls/daml:button[@id='AcceptanceAddin_BulkDensityQAButton']", ns)
    assert button is not None
    assert button.attrib["caption"] == "容重样点质检"
    assert button.attrib["className"] == "BulkDensityQAButton"

    source = COMMAND.read_text(encoding="utf-8")
    assert "class BulkDensityQAButton : Button" in source
    assert 'ToolboxLocator.Open("BulkDensitySamplingQA")' in source

    menu = root.find(".//daml:menus/daml:menu[@id='AcceptanceAddin_MoreMenu']", ns)
    assert menu is not None
    refs = {node.attrib["refID"] for node in menu.findall("daml:button", ns)}
    assert "AcceptanceAddin_BulkDensityQAButton" in refs


def test_spacing_exception_review_json_is_optional_and_mapped_to_the_tool_core():
    source = TOOLBOX.read_text(encoding="utf-8")
    assert '_p("spacing_reviews_json", "间距不足例外审核记录JSON（可选；仍需人工判定）", "DEFile", required="Optional")' in source
    assert "spacing_exception_reviews_json=parameters[12].valueAsText" in source


def test_all_ribbon_buttons_map_to_tools_registered_in_the_python_toolbox():
    ns = {"daml": "http://schemas.esri.com/DADF/Registry"}
    root = ET.parse(CONFIG).getroot()
    buttons = {
        button.attrib["id"]: button.attrib["className"]
        for button in root.findall(".//daml:controls/daml:button[@className]", ns)
    }
    expected = {
        "AcceptanceAddin_ProjectButton": ("ProjectSetupButton", "CreateProject"),
        "AcceptanceAddin_VectorButton": ("VectorImportButton", "ImportVector"),
        "AcceptanceAddin_VectorBatchButton": ("VectorBatchImportButton", "BatchImportVector"),
        "AcceptanceAddin_DataButton": ("DataIntakeButton", "ImportCAD"),
        "AcceptanceAddin_RTKButton": ("RTKImportButton", "ImportRTK"),
        "AcceptanceAddin_RemoteSensingButton": ("RemoteSensingButton", "Sentinel2CatalogSearch"),
        "AcceptanceAddin_SamplingButton": ("SamplingDesignButton", "CompositeSampling"),
        "AcceptanceAddin_BulkDensityButton": ("BulkDensityDesignButton", "BulkDensitySampling"),
        "AcceptanceAddin_QAButton": ("QualityCheckButton", "QualityCheck"),
        "AcceptanceAddin_BulkDensityQAButton": ("BulkDensityQAButton", "BulkDensitySamplingQA"),
        "AcceptanceAddin_DeliveryButton": ("DeliveryButton", "DeliveryCheck"),
        "AcceptanceAddin_ImageryAccuracyButton": ("ImageryAccuracyButton", "ImageryAccuracyCheck"),
    }
    parcel_expected = {
        "AcceptanceAddin_ParcelAudit": ("ParcelAuditButton", "ParcelAudit"),
        "AcceptanceAddin_ParcelPrepare": ("ParcelPrepareButton", "ParcelPrepare"),
        "AcceptanceAddin_ParcelMaps": ("ParcelMapsButton", "ParcelMaps"),
        "AcceptanceAddin_AmapPoiSearch": ("AmapPoiSearchButton", "POIReferenceSearch"),
    }
    grid_expected = {
        "AcceptanceAddin_DemTerrainButton": ("DemTerrainButton", "ExtractDemTerrain"),
        "AcceptanceAddin_SamplingCoordinatesButton": ("SamplingCoordinatesButton", "ExportSamplingCoordinates"),
    }
    # 一键 GP 工具由侧栏流程调用，Ribbon 只保留侧栏入口，避免重复入口。
    ui_only = {"AcceptanceAddin_WizardPaneButton": "WizardPaneButton",
               "AcceptanceAddin_SamplingWorkspaceButton": "SamplingWorkspaceButton",
               "AcceptanceAddin_ParcelLocate": "ParcelLocateButton",
               "AcceptanceAddin_AmapPoiCredential": "AmapPoiCredentialButton"}
    all_gp = {**expected, **parcel_expected, **grid_expected}
    assert buttons == {**{button_id: class_name for button_id, (class_name, _) in all_gp.items()}, **ui_only}

    command_mapping = {}
    for command_file in COMMANDS.glob("*.cs"):
        source = command_file.read_text(encoding="utf-8")
        for class_name, body in re.findall(r"class\s+(\w+)\s*:\s*Button\s*\{([\s\S]*?)\}", source):
            match = re.search(r'ToolboxLocator\.Open\("([A-Za-z0-9_]+)"(?:,\s*"([A-Za-z0-9_]+\.pyt)")?\)', body)
            if match:
                tool_name, toolbox_name = match.groups()
                assert class_name not in command_mapping
                command_mapping[class_name] = (tool_name, toolbox_name or TOOLBOX.name)
            elif class_name == "ParcelLocateButton":
                assert "ParcelLocatorWindow.Open()" in body
            elif class_name == "WizardPaneButton":
                assert "AcceptanceWorkspacePane.Show" in body
            elif class_name == "SamplingWorkspaceButton":
                assert 'AcceptanceWorkspacePane.Show("sampling")' in body
            elif class_name == "AmapPoiCredentialButton":
                assert "AmapPoiCredentialWindow.Open()" in body
            else:
                raise AssertionError("Ribbon command has no recognized entry: " + class_name)
    expected_mapping = {class_name: (tool, TOOLBOX.name) for class_name, tool in expected.values()}
    expected_mapping.update({class_name: (tool, "ParcelTools.pyt") for class_name, tool in parcel_expected.values()})
    expected_mapping.update({class_name: (tool, "GridSampling.pyt") for class_name, tool in grid_expected.values()})
    assert command_mapping == expected_mapping

    for toolbox_name in {box for _, box in command_mapping.values()}:
        toolbox_ast = ast.parse((ROOT / "toolboxes" / toolbox_name).read_text(encoding="utf-8"))
        toolbox_class = next(node for node in toolbox_ast.body if isinstance(node, ast.ClassDef) and node.name == "Toolbox")
        init_method = next(node for node in toolbox_class.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        tools_assignment = next(node for node in init_method.body if isinstance(node, ast.Assign)
                                and any(isinstance(target, ast.Attribute) and target.attr == "tools" for target in node.targets))
        registered_tools = {item.id for item in tools_assignment.value.elts if isinstance(item, ast.Name)}
        assert {tool for tool, box in command_mapping.values() if box == toolbox_name} <= registered_tools


def test_sampling_wizard_dock_pane_is_declared_and_compiles_into_the_addin():
    ns = {"daml": "http://schemas.esri.com/DADF/Registry"}
    root = ET.parse(CONFIG).getroot()
    panes = root.findall(".//daml:dockPanes/daml:dockPane", ns)
    assert [pane.attrib["className"] for pane in panes] == ["AcceptanceWorkspacePane"]
    pane_id = panes[0].attrib["id"]
    source = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    assert "class SamplingWizardPane : UserControl" in source
    workspace = (ROOT/'addin/AcceptanceAddin/AcceptanceWorkspacePane.cs').read_text(encoding='utf-8')
    assert 'PaneId = "{}"'.format(pane_id) in workspace
    assert "protected override Control OnCreateContent()" in workspace
    assert "SamplingWizardPane" in workspace and "ChatPane" in workspace
    assert 'ToolboxLocator.GetToolPath("GridSamplingOneClick", "GridSampling.pyt")' in source


def test_sampling_wizard_keeps_export_action_and_status_outside_the_scrolling_steps():
    source = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    assert "var bodyScroll = new ScrollViewer" in source
    assert "var primaryFooter = new StackPanel" in source
    assert "DockPanel.SetDock(primaryFooter, Dock.Bottom)" in source
    assert "DockPanel.SetDock(bodyScroll, Dock.Top)" in source
    assert "deliveryPanel.Children.Add(_run)" in source
    assert "primaryFooter.Children.Add(_run)" not in source
    assert "primaryFooter.Children.Add(_status)" in source
    assert "root.Children.Add(_run)" not in source


def test_wizard_main_action_creates_map_previews_without_exposing_technical_forms():
    source = (ROOT / "addin/AcceptanceAddin/SamplingWizardPane.cs").read_text(encoding="utf-8")
    assert 'await Run("preview")' in source
    assert "primaryFooter.Children.Add(_prepare)" in source
    assert "root.Children.Add(_prepare)" not in source
    assert "root.Children.Add(_imageryKind)" not in source
    assert "root.Children.Add(_org)" not in source
    assert "root.Children.Add(_outDir)" not in source
    assert "LoadMapPreviews(report.RootElement)" in source
    assert "AutoConfigureSource()" in source
    assert 'taskAction == "export" && string.IsNullOrWhiteSpace(_org.Text)' in source


def test_rendered_preview_is_bound_before_optional_map_layer_loading():
    source = (ROOT / "addin/AcceptanceAddin/SamplingWizardPane.cs").read_text(encoding="utf-8")
    method = source.split("private async Task LoadPreparedLayers", 1)[1].split("internal async Task ReloadAfterAdjustment", 1)[0]
    assert method.index("_previewTaskRoot = taskRoot") < method.index("if (MapView.Active == null)")
    assert method.index("_previewFingerprint =") < method.index("QueuedTask.Run")


def test_oneclick_gp_tool_has_only_the_sampling_wizard_ribbon_entry():
    ns = {"daml": "http://schemas.esri.com/DADF/Registry"}
    root = ET.parse(CONFIG).getroot()
    controls = root.find(".//daml:controls", ns)
    assert controls.find("daml:button[@id='AcceptanceAddin_OneClickButton']", ns) is None

    wizard_source = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    assert 'ToolboxLocator.GetToolPath("GridSamplingOneClick", "GridSampling.pyt")' in wizard_source
    command_source = (ROOT / "addin" / "AcceptanceAddin" / "Commands" / "WizardButtons.cs").read_text(encoding="utf-8")
    assert "class OneClickButton" not in command_source


def test_tianditu_snapshot_cli_keeps_image_and_vector_sources_distinct(monkeypatch, tmp_path):
    import importlib.util
    import sys
    import types

    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.setitem(sys.modules, "arcpy", types.SimpleNamespace(
        Extent=lambda *args, **kwargs: (args, kwargs),
        SpatialReference=lambda wkid: wkid))
    spec = importlib.util.spec_from_file_location("snapshot_cli", ROOT / "acceptance" / "tianditu_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import acceptance.imagery as imagery
    calls = []

    def prepare(out_dir, wkid, **kwargs):
        calls.append(kwargs["provider"])
        output = Path(out_dir) / "result.tif"
        output.write_bytes(b"tif")
        return {"output": str(output), "status": "PASS", "coverage_status": "PASS", "zoom": 18}

    monkeypatch.setattr(imagery, "prepare_imagery", prepare)
    monkeypatch.setattr(module, "_tianditu_key", lambda: "test-key")
    for index, source in enumerate(("tianditu_image", "tianditu_vector")):
        output = tmp_path / str(index) / "snapshot.tif"
        monkeypatch.setattr(sys, "argv", ["snapshot", "--out", str(output), "--wkid", "3857",
            "--extent", "0", "0", "100", "100", "--source", source])
        assert module.main() == 0
        assert output.parent.joinpath("result.tif").is_file()
    assert calls == ["tianditu", "tianditu_vector"]


def test_acceptance_delivery_searches_default_preview_directory():
    source = (ROOT / "ai" / "addin" / "MapActions.cs").read_text(encoding="utf-8")
    service = (ROOT / "addin/AcceptanceAddin/WorkbenchJobService.cs").read_text(encoding="utf-8")
    assert 'Path.Combine(root, "ProAiBridge")' in service
    assert 'Directory.GetDirectories(bridgeRoot, "acceptance_*")' in service
    assert 'AcceptanceAddin.AcceptanceDelivery.Read' in source
    assert 'Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),\n                                "ArcGIS", "ProAiBridge", "acceptance_"' in source


def test_saved_map_snapshots_are_not_evicted_after_success():
    source = (ROOT / "ai" / "addin" / "MapActions.cs").read_text(encoding="utf-8")
    create = source.split("internal static Result Create(int wkid", 1)[1].split("private static Result ReadCache", 1)[0]
    assert ".Skip(5)" not in create
    assert "Directory.Delete(safeFolder, true)" in create
    assert "safeFolder.StartsWith(safeRoot, StringComparison.OrdinalIgnoreCase)" in create


def test_snapshot_timeout_waits_for_child_before_cleaning_and_preserves_primary_error():
    source = (ROOT / "ai" / "addin" / "MapActions.cs").read_text(encoding="utf-8")
    create = source.split("internal static Result Create(int wkid", 1)[1].split("private static Result ReadCache", 1)[0]
    assert "process.WaitForExit(5000)" in create
    assert "catch (IOException) { }" in create
    assert "catch (UnauthorizedAccessException) { }" in create


def test_basemap_cleanup_is_map_scoped_and_runs_when_map_view_closes():
    bridge_root = ROOT / "ai" / "addin"
    map_actions = (bridge_root / "MapActions.cs").read_text(encoding="utf-8")
    bridge = (bridge_root / "Bridge.cs").read_text(encoding="utf-8")
    cleanup = map_actions.split("internal static bool RemoveSessionBasemap", 1)[1].split(
        "internal static Layer FindLayer", 1
    )[0]
    assert "ConcurrentDictionary<string, bool>" in map_actions
    assert "map.Layers.Where(layer => uris.Contains(layer.URI))" in cleanup
    assert "BasemapPreview.CloseMap(mapKey)" in cleanup
    assert "TrackBasemapLayer(mapKey, layer, false)" in map_actions
    assert "TrackBasemapLayer(mapKey, group, true)" in map_actions
    assert "RemoveSessionBasemap(map, mapKey, mapClosed: true)" in map_actions
    assert "MapClosedEvent.Subscribe" in bridge
    assert "MapActions.CloseMap(map)" in bridge
    assert "FrameworkApplication.Panes.OfType<IMapPane>()" in bridge
    assert "!ReferenceEquals(pane, args.MapPane)" in bridge


def test_wizard_disambiguates_duplicate_layers_and_selection_scope():
    source = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    toolbox = (ROOT / "toolboxes" / "GridSampling.pyt").read_text(encoding="utf-8")
    core = (ROOT / "acceptance" / "oneclick.py").read_text(encoding="utf-8")
    assert "table.GetPath().ToString()" in source
    assert "只处理当前已选要素" in source
    assert "GetSelection()" in source and "GetObjectIDs()" in source
    assert "selection_oids=parameters[22].valueAsText or None" in toolbox
    toolbox_tree = ast.parse(toolbox)
    command = next(node for node in toolbox_tree.body if isinstance(node, ast.ClassDef) and node.name == "GridSamplingOneClick")
    parameters = next(node for node in command.body if isinstance(node, ast.FunctionDef) and node.name == "getParameterInfo")
    outputs = next(node for node in parameters.body if isinstance(node, ast.Return)).value.elts
    report_index = next(i for i, node in enumerate(outputs) if isinstance(node, ast.Name) and node.id == "report")
    assert f'SetParameterAsText({report_index}, report_path)' in toolbox
    assert any(isinstance(node, ast.Name) and node.id == "action" for node in outputs)


def test_python_packager_includes_required_template():
    import importlib.util
    spec = importlib.util.spec_from_file_location("package_contract", ROOT / "addin" / "package.py")
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    contents = dict((archive, path) for path, archive in packager.entries())
    template = "Install/templates/补充耕地鉴定图_A4.pagx"
    assert contents[template].read_bytes() == (ROOT / "templates" / "补充耕地鉴定图_A4.pagx").read_bytes()
    metadata = {name for name in contents if name.endswith(".pyt.xml")}
    assert metadata == {
        "Install/toolboxes/GridSampling.pyt.xml",
        "Install/toolboxes/SupplementaryCultivatedLandAcceptance.pyt.xml",
    }


def test_field_survey_help_is_defined_in_the_toolbox_source_not_a_generated_stub():
    toolbox = (ROOT / "toolboxes" / "SupplementaryCultivatedLandAcceptance.pyt").read_text(encoding="utf-8")
    assert "只读检查踏勘 ZIP 中的 GeoJSON/Shapefile 点数据" in toolbox
    assert not (ROOT / "toolboxes" / "SupplementaryCultivatedLandAcceptance.InspectFieldSurveyBundle.pyt.xml").exists()


def test_addin_build_writes_one_canonical_package_without_bin_copy():
    script = (ROOT / "addin" / "package.ps1").read_text(encoding="utf-8")
    assert "package.py" in script and "build_roslyn.py" in script
    assert "ZipFile" not in script and "CreateEntryFromFile" not in script


def test_compiled_assembly_metadata_uses_daml_release_version(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('addin_release_metadata', ROOT / 'addin/build_roslyn.py')
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    daml = tmp_path / 'Config.daml'
    daml.write_text('<ArcGIS><AddInInfo version="2.7.4" /></ArcGIS>', encoding='utf-8')
    metadata = builder.release_metadata(daml)
    assert 'AssemblyFileVersion("2.7.4.0")' in metadata
    assert 'AssemblyVersion("2.7.4.0")' in metadata
    assert 'AssemblyInformationalVersion("2.7.4")' in metadata


def test_wizard_has_folder_discovery_and_current_imagery_entry():
    pane = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    toolbox = ast.parse((ROOT / "toolboxes" / "GridSampling.pyt").read_text(encoding="utf-8"))
    assert any(isinstance(node, ast.ClassDef) and node.name == "DiscoverProjectData" for node in toolbox.body)
    assert "discover.Click += async (_, __) => await DiscoverFolder()" in pane
    assert 'GetToolPath("DiscoverProjectData", "GridSampling.pyt")' in pane
    assert "currentImagery.Click += async (_, __) => await LoadCurrentImagery()" in pane
    assert '"factor_sources"' in (ROOT / "toolboxes" / "GridSampling.pyt").read_text(encoding="utf-8")
    assert "_factorChoices" in pane


def test_wizard_stages_preparation_review_and_export():
    pane = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    assert 'Run("preview")' in pane and 'Run("export")' in pane
    assert "LoadPreparedLayers" in pane
    assert "_designConfirmed" in pane and '_previewFingerprint = report.GetProperty("输入指纹")' in pane
    toolbox = (ROOT / "toolboxes" / "GridSampling.pyt").read_text(encoding="utf-8")
    assert '"review_confirmation"' in toolbox and '"project_number"' in toolbox


def test_point_adjustment_tool_has_a_registered_map_click_path():
    ns = {"daml": "http://schemas.esri.com/DADF/Registry"}
    root = ET.parse(CONFIG).getroot()
    assert root.find(".//daml:tool[@className='PointAdjustmentTool']", ns) is not None
    source = (ROOT / "addin" / "AcceptanceAddin" / "PointAdjustmentTool.cs").read_text(encoding="utf-8")
    assert "OnSketchCompleteAsync" in source and '"AdjustDesignPoint"' in source


def test_adjustment_is_bound_to_preview_task_instead_of_live_output_text():
    pane = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    tool = (ROOT / "addin" / "AcceptanceAddin" / "PointAdjustmentTool.cs").read_text(encoding="utf-8")
    assert "PointAdjustmentTool.TaskRoot = _previewTaskRoot" in pane
    assert "ResetPreviewBinding" in pane
    assert "ReloadAfterAdjustment(taskRoot)" in tool
    assert "ExpectedSignature" in tool and "ExpectedRevision" in tool


def test_per_unit_imagery_has_a_wizard_entry_and_toolbox_parameter():
    pane = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    toolbox = (ROOT / "toolboxes" / "GridSampling.pyt").read_text(encoding="utf-8")
    assert "Content = _unitImageryPanel" in pane and "_unitImageryChoices" in pane
    assert '"unit_imagery_sources"' in toolbox


def test_technical_failure_keeps_verified_preview_visible_but_blocks_export():
    service = (ROOT / "addin" / "AcceptanceAddin" / "WorkbenchJobService.cs").read_text(encoding="utf-8")
    pane = (ROOT / "addin" / "AcceptanceAddin" / "SamplingWizardPane.cs").read_text(encoding="utf-8")
    current_report = service.split("internal static JsonObject ReadCurrentReport", 1)[1].split(
        "private static bool Alive", 1)[0]
    receipt = service.split("internal static JsonObject Read(string root", 1)[1].split(
        "internal static void RequestStop", 1)[0]
    run = pane.split("private async Task Run(string taskAction)", 1)[1].split(
        "private async Task LoadPreparedLayers", 1)[0]
    load = pane.split("private async Task LoadPreparedLayers", 1)[1].split(
        "internal async Task ReloadAfterAdjustment", 1)[0]

    assert '["can_open_preview"] = false' in receipt
    assert 'result["can_open_preview"] = mapped == "Prepared"' in receipt
    assert "inputFingerprint.Length != 64" in current_report
    assert "inputFingerprint.Any(c => !Uri.IsHexDigit(c))" in current_report
    assert receipt.index("foreach (var output in outputs)") < receipt.index(
        'result["can_open_preview"] = mapped == "Prepared"')
    assert 'mapped == "Succeeded" || (mapped == "Prepared" && !technicalFailed)' in receipt
    receipt_error = receipt.split("catch (Exception ex) when", 1)[1].split("return result;", 1)[0]
    assert 'result["can_open_preview"] = false' in receipt_error
    assert 'taskAction == "preview"' in run and '"can_open_preview"' in run
    assert '"can_open_result"' in run
    assert 'technical_status' in load and '"技术状态"' in load
    assert '_previewCanExport = !technicalFailed' in load

