using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using AcceptanceAddin.Ui;

namespace AcceptanceAddin
{
    // 独立负责编辑候选；采样面板只接收确认后的新来源。
    internal sealed class ParcelEditingPanel : StackPanel
    {
        private readonly Func<Task<(string Path, string IdField, string LayerUri)>> _source;
        private readonly Func<string, string, string, string, Task> _useSource;
        private readonly TextBox _operator = new TextBox { MaxLength = 100, Margin = new Thickness(0, 3, 0, 5) };
        private readonly TextBlock _feedback = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 4) };
        private readonly Button _merge = new Button { Content = "合并所选", Margin = new Thickness(2) };
        private readonly Button _split = new Button { Content = "画线分割", Margin = new Thickness(2) };
        private readonly Button _confirm = new Button { Content = "确认使用", Margin = new Thickness(2) };
        private readonly Button _cancel = new Button { Content = "取消候选", Margin = new Thickness(2) };
        private readonly Button _undo = new Button { Content = "撤销上次编辑", Margin = new Thickness(2) };
        private readonly Stack<JsonObject> _history = new Stack<JsonObject>();
        private JsonObject _pending;
        private Map _previewMap;
        private string _previewUri, _sourceUri;
        private bool _sourceVisible, _switchingSource, _drawing;
        private int _generation;

        internal ParcelEditingPanel(Func<Task<(string Path, string IdField, string LayerUri)>> source, Func<string, string, string, string, Task> useSource)
        {
            _source = source; _useSource = useSource;
            UiTheme.Primary(_confirm);
            UiTheme.Quiet(_cancel);
            UiTheme.Quiet(_undo);
            _feedback.Style = UiTheme.CaptionStyle;
            UiTheme.WatchStatus(_feedback);
            Children.Add(new TextBlock { Text = "操作人", Style = UiTheme.CaptionStyle }); Children.Add(_operator);
            var actions = new WrapPanel();
            foreach (var button in new[] { _merge, _split, _confirm, _cancel, _undo }) actions.Children.Add(button);
            Children.Add(actions); Children.Add(_feedback);
            _merge.Click += async (_, __) => await BeginEdit(false);
            _split.Click += async (_, __) => await BeginEdit(true);
            _confirm.Click += async (_, __) => await Resolve("confirm");
            _cancel.Click += async (_, __) =>
            {
                if (_drawing) { CancelDrawing(); await FrameworkApplication.SetCurrentToolAsync("esri_mapping_exploreTool"); }
                else await Resolve("cancel");
            };
            _undo.Click += async (_, __) => await Resolve("undo");
            WorkbenchJobService.Changed += () => Dispatcher.InvokeAsync(Refresh);
            Refresh();
        }

        private void Refresh()
        {
            var idle = !WorkbenchJobService.IsBusy;
            _operator.IsEnabled = idle;
            _merge.IsEnabled = _split.IsEnabled = idle && _pending == null && !_drawing;
            _confirm.IsEnabled = idle && (string)_pending?["status"] != "CANCELLED";
            _cancel.IsEnabled = idle && (string)_pending?["status"] != "COMMITTED";
            _confirm.Visibility = _pending == null ? Visibility.Collapsed : Visibility.Visible;
            _cancel.Visibility = _pending == null && !_drawing ? Visibility.Collapsed : Visibility.Visible;
            _cancel.Content = _drawing ? "取消绘制" : "取消候选";
            _undo.IsEnabled = idle && _pending == null;
            _undo.Visibility = _history.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        }

        internal async Task SourceChanged()
        {
            if (_switchingSource) return;
            CancelDrawing(); _generation++;
            if ((string)_pending?["status"] == "COMMITTED")
            { _feedback.Text = "已确认的编辑版本待加载，请回到原工程、地图和来源后再次确认使用。"; Refresh(); return; }
            _pending = null;
            try { await RemovePreview(); _feedback.Text = ""; }
            catch (Exception ex) { _feedback.Text = "旧候选图层清理失败：" + ex.Message; }
            Refresh();
        }

        private void CancelDrawing()
        {
            if (_drawing) _generation++;
            _drawing = false; ParcelSplitTool.Submit = null; ParcelSplitTool.Cancelled = null;
            Refresh();
        }

        internal static void EnsureContext(string project, string map)
        {
            if (string.IsNullOrWhiteSpace(project) || string.IsNullOrWhiteSpace(map)) throw new InvalidOperationException("编辑上下文缺失。");
            ProAiBridge.PaneState.EnsureProject(project, ArcGIS.Desktop.Core.Project.Current?.URI ?? "");
            ProAiBridge.PaneState.EnsureMap(map, MapView.Active?.Map?.URI ?? "");
        }

        private async Task<(string Path, string IdField, string LayerUri, Map Map, long[] Ids, string Project)> Context()
        {
            var project = ArcGIS.Desktop.Core.Project.Current?.URI;
            var source = await _source();
            var map = MapView.Active?.Map ?? throw new InvalidOperationException("请先打开图斑所在地图。");
            EnsureContext(project, map.URI);
            var ids = await QueuedTask.Run(() =>
            {
                EnsureContext(project, map.URI);
                var layer = map.GetLayersAsFlattenedList().OfType<FeatureLayer>().SingleOrDefault(l => l.URI == source.LayerUri)
                    ?? throw new InvalidOperationException("请先将图斑加入当前地图，再选择要编辑的图斑。");
                using var selection = layer.GetSelection();
                return selection.GetObjectIDs().OrderBy(v => v).ToArray();
            });
            return (source.Path, source.IdField, source.LayerUri, map, ids, project);
        }

        private async Task BeginEdit(bool split)
        {
            try
            {
                if (WorkbenchJobService.IsBusy || _pending != null) return;
                CancelDrawing();
                if (string.IsNullOrWhiteSpace(_operator.Text)) throw new InvalidOperationException("请填写操作人。");
                var context = await Context();
                if ((split && context.Ids.Length != 1) || (!split && context.Ids.Length < 2))
                    throw new InvalidOperationException(split ? "请在地图只选择一块图斑。" : "请在地图选择至少两块相邻图斑。");
                var generation = _generation;
                async Task Submit(Polyline line)
                {
                    try
                    {
                        if (generation != _generation || _pending != null) throw new InvalidOperationException("编辑候选或来源已变化，请重新选择图斑。");
                        _drawing = false; ParcelSplitTool.Cancelled = null;
                        var current = await Context();
                        if (current.Path != context.Path || current.IdField != context.IdField || current.Project != context.Project ||
                            current.Map.URI != context.Map.URI || !current.Ids.SequenceEqual(context.Ids))
                            throw new InvalidOperationException("地图或选择集已变化，请重新选择图斑。");
                        using var job = WorkbenchJobService.Begin(split ? "图斑分割候选" : "图斑合并候选", context.Path);
                        try
                        {
                            if (_pending != null) throw new InvalidOperationException("请先处理已有编辑候选。");
                            job.Running();
                            var home = ArcGIS.Desktop.Core.Project.Current.HomeFolderPath;
                            var folder = Path.Combine(home, "图斑编辑", DateTime.Now.ToString("yyyyMMdd_HHmmss") + "_" + Guid.NewGuid().ToString("N").Substring(0, 8));
                            var data = await Execute("ParcelEditPreview", context.Path, context.IdField,
                                JsonSerializer.Serialize(context.Ids), split ? "split" : "merge", folder, line?.ToJson() ?? "");
                            if (generation != _generation) throw new InvalidOperationException("来源已变化，候选未加载。");
                            EnsureContext(context.Project, context.Map.URI);
                            data["_ui_project"] = context.Project; data["_ui_map"] = context.Map.URI;
                            _previewMap = context.Map; _sourceUri = context.LayerUri;
                            _previewUri = await QueuedTask.Run(() =>
                            {
                                EnsureContext(context.Project, context.Map.URI);
                                var original = context.Map.GetLayersAsFlattenedList().Single(l => l.URI == context.LayerUri);
                                using var selected = ((FeatureLayer)original).GetSelection();
                                if (!selected.GetObjectIDs().OrderBy(v => v).SequenceEqual(context.Ids))
                                    throw new InvalidOperationException("选择集已变化，候选未显示。");
                                _sourceVisible = original.IsVisible;
                                var preview = LayerFactory.Instance.CreateLayer(new Uri((string)data["output"]), context.Map, 0, "图斑编辑候选") as FeatureLayer
                                    ?? throw new InvalidOperationException("候选图层未加载。");
                                var outline = SymbolFactory.Instance.ConstructStroke(ColorFactory.Instance.CreateRGBColor(30, 100, 230), 1.5);
                                var symbol = SymbolFactory.Instance.ConstructPolygonSymbol(ColorFactory.Instance.CreateRGBColor(0, 0, 0, 0), SimpleFillStyle.Null, outline);
                                preview.SetRenderer(new ArcGIS.Core.CIM.CIMSimpleRenderer { Symbol = symbol.MakeSymbolReference() });
                                original.SetVisibility(false);
                                return preview.URI;
                            });
                            _pending = data;
                            var qa = data["qa"];
                            _feedback.Text = "候选已显示：" + data["count"] + " 块，面积 " + ((double)qa["area_before_m2"]).ToString("0.##") +
                                " → " + ((double)qa["area_after_m2"]).ToString("0.##") + " ㎡。临时编号与原地块关联已保留。" +
                                " 清空待重建字段：" + string.Join("、", data["cleared_fields"].AsArray().Select(v => (string)v)) +
                                "；失效规则：" + string.Join("、", data["invalidated_rules"].AsArray().Select(v => (string)v)) + "。核对地图后确认。";
                            job.Finish("Prepared");
                        }
                        catch { job.Finish("Failed"); throw; }
                    }
                    catch (Exception ex) { _feedback.Text = ex.Message; }
                    finally { Refresh(); }
                }
                if (split)
                {
                    _drawing = true;
                    ParcelSplitTool.Submit = Submit;
                    ParcelSplitTool.Cancelled = () => Dispatcher.InvokeAsync(CancelDrawing);
                    await FrameworkApplication.SetCurrentToolAsync("AcceptanceAddin_ParcelSplit");
                    _feedback.Text = "从图斑外开始画线，穿过图斑后双击结束；Esc取消绘制。";
                }
                else await Submit(null);
            }
            catch (Exception ex) { CancelDrawing(); _feedback.Text = ex.Message; }
            finally { Refresh(); }
        }

        private async Task Resolve(string action)
        {
            try
            {
                if (WorkbenchJobService.IsBusy) return;
                var data = action == "undo" ? _history.Peek() : _pending;
                if (data == null) return;
                var project = (string)data["_ui_project"]; var map = (string)data["_ui_map"];
                EnsureContext(project, map);
                var current = await _source();
                var expected = (string)data[action == "undo" ? "output" : "source"];
                var finalState = action == "confirm" ? "COMMITTED" : action == "cancel" ? "CANCELLED" : "UNDONE";
                var finalized = (string)data["status"] == finalState;
                var destination = (string)data[action == "undo" ? "source" : "output"];
                if (!ProAiBridge.GpJob.SameFeatureSource(current.Path, expected) &&
                    !(finalized && ProAiBridge.GpJob.SameFeatureSource(current.Path, destination)))
                    throw new InvalidOperationException("当前来源已变化，不能应用这个编辑版本。");
                using var job = WorkbenchJobService.Begin("图斑编辑" + action, expected);
                try
                {
                    job.Running();
                    var result = await Execute("ParcelEditResolve", (string)data["manifest"],
                        (string)data["token"], finalized ? "verify" : action, _operator.Text.Trim());
                    if ((string)result["status"] != finalState) throw new InvalidOperationException("编辑版本状态已变化，请重新读取。");
                    result["_ui_project"] = project; result["_ui_map"] = map;
                    // 先保留新token，界面加载失败时可重试，不重复提交后端状态。
                    if (action == "undo") { _history.Pop(); _history.Push(result); }
                    else _pending = result;
                    EnsureContext(project, map);
                    if (action == "cancel") { await RemovePreview(); _pending = null; }
                    else
                    {
                        _switchingSource = true;
                        try { await _useSource((string)result[action == "undo" ? "source" : "output"],
                            (string)result[action == "undo" ? "source_id_field" : "id_field"], project, map); }
                        finally { _switchingSource = false; }
                        if (action == "confirm") { _history.Push(result); _pending = null; _previewUri = null; }
                        else _history.Pop();
                    }
                    _feedback.Text = action == "cancel" ? "候选已取消，原数据不变。" : action == "undo" ?
                        "已回到上一来源，请重新生成采样预览。" : "已使用新工作图斑，旧设计确认已撤销，请生成采样预览。";
                    job.Finish("Succeeded");
                }
                catch { job.Finish("Failed"); throw; }
            }
            catch (Exception ex) { _feedback.Text = ex.Message; }
            finally { Refresh(); }
        }

        private async Task RemovePreview()
        {
            var map = _previewMap; var uri = _previewUri; var source = _sourceUri;
            if (map == null || uri == null) return;
            await QueuedTask.Run(() =>
            {
                var layers = map.GetLayersAsFlattenedList();
                var preview = layers.FirstOrDefault(l => l.URI == uri);
                if (preview != null) map.RemoveLayer(preview);
                layers.FirstOrDefault(l => l.URI == source)?.SetVisibility(_sourceVisible);
            });
            _previewMap = null; _previewUri = null; _sourceUri = null;
        }

        private static async Task<JsonObject> Execute(string tool, params string[] args)
        {
            var result = await Geoprocessing.ExecuteToolAsync(ToolboxLocator.GetToolPath(tool, "ParcelTools.pyt"),
                Geoprocessing.MakeValueArray(args), null, CancellationToken.None, null, GPExecuteToolFlags.GPThread);
            if (result == null || result.IsFailed)
                throw new InvalidOperationException(string.Join("；", result?.Messages?.Select(m => m.Text) ?? new[] { "图斑编辑未完成。" }));
            return JsonNode.Parse(result.ReturnValue).AsObject();
        }
    }
}
