using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Security.Cryptography;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using ArcGIS.Core.Data;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using AcceptanceAddin.Ui;
using UiKit = AcceptanceAddin.Ui.Ui;

namespace AcceptanceAddin
{
    // Code-only WPF keeps the window compatible with the supported Roslyn build.
    internal sealed class ParcelLocatorWindow : Window
    {
        private static ParcelLocatorWindow _instance;
        private const string ObjectIdSearchToken = "\0OBJECT_ID";
        private readonly ComboBox _layers = new ComboBox { Margin = new Thickness(0, 4, 0, 8) };
        private readonly ComboBox _fields = new ComboBox { Margin = new Thickness(0, 4, 0, 8) };
        private readonly List<string> _fieldValues = new List<string>();
        private readonly TextBox _query = new TextBox { Margin = new Thickness(0, 4, 0, 8) };
        private readonly CheckBox _exact = new CheckBox { Content = "精确匹配（推荐）", IsChecked = true };
        private readonly TextBlock _status = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0) };
        private readonly ListView _list = new ListView { Margin = new Thickness(0, 10, 0, 8) };
        private readonly ObservableCollection<Hit> _hits = new ObservableCollection<Hit>();
        private readonly StackPanel _inputs = new StackPanel();
        private Map _map;
        private List<FeatureLayer> _available = new List<FeatureLayer>();
        private bool _loading;
        private readonly CheckBox _selectedOnly = new CheckBox { Content = "仅检索执行时选中的要素" };
        private readonly ComboBox _issues = new ComboBox { Margin = new Thickness(0, 5, 0, 5) };
        private string _reportPath, _groupUri, _taskRoot, _reportDigest;
        private readonly Dictionary<string, string> _unitProjects = new Dictionary<string, string>();
        private readonly NavigationGate _navigationGate = new NavigationGate();

        internal static void Open(string reportPath = null, string groupUri = null, string taskRoot = null)
        {
            if (_instance != null)
            {
                if (!_instance._inputs.IsEnabled) { _instance.Activate(); return; }
                _instance._reportPath = reportPath; _instance._groupUri = groupUri; _instance._taskRoot = taskRoot;
                _instance.Activate(); _ = _instance.LoadLayers(); return;
            }
            _instance = new ParcelLocatorWindow { _reportPath = reportPath, _groupUri = groupUri, _taskRoot = taskRoot };
            _instance.Closed += (_, __) => _instance = null;
            _instance.Show();
        }

        private ParcelLocatorWindow()
        {
            Title = "快速定位地块与检查问题"; Width = 720; Height = 740; MinWidth = 570; MinHeight = 580;
            Owner = Application.Current.MainWindow; ShowInTaskbar = false;
            var root = new DockPanel { Margin = new Thickness(16) };
            UiTheme.Apply(root);
            Background = UiTheme.Brush(UiTheme.Page);
            _status.Style = UiTheme.CaptionStyle;
            UiTheme.WatchStatus(_status);
            _list.Background = UiTheme.Brush(UiTheme.Surface);
            _list.BorderBrush = UiTheme.Brush(UiTheme.Border);
            _list.BorderThickness = new Thickness(1);
            var header = UiKit.Section("快速定位地块与检查问题");
            DockPanel.SetDock(header, Dock.Top); root.Children.Add(header);
            var footer = new StackPanel();
            var locate = new System.Windows.Controls.Button { Content = "定位所选结果", Padding = new Thickness(12, 6, 12, 6) };
            UiTheme.Primary(locate);
            locate.Click += async (_, __) => await Locate();
            var navigation = new StackPanel { Orientation = Orientation.Horizontal };
            var previous = new System.Windows.Controls.Button { Content = "上一块", Padding = new Thickness(12, 6, 12, 6), Margin = new Thickness(0, 0, 8, 0) };
            var next = new System.Windows.Controls.Button { Content = "下一块", Padding = new Thickness(12, 6, 12, 6), Margin = new Thickness(0, 0, 8, 0) };
            previous.Click += async (_, __) => await Navigate(-1); next.Click += async (_, __) => await Navigate(1);
            navigation.Children.Add(previous); navigation.Children.Add(next); navigation.Children.Add(locate);
            footer.Children.Add(navigation); footer.Children.Add(_status);
            DockPanel.SetDock(footer, Dock.Bottom); root.Children.Add(footer);
            _inputs.Children.Add(new TextBlock { Text = "地图图层", Style = UiTheme.CaptionStyle }); _inputs.Children.Add(_layers);
            _inputs.Children.Add(new TextBlock { Text = "地块编号字段", Style = UiTheme.CaptionStyle }); _inputs.Children.Add(_fields);
            _inputs.Children.Add(new TextBlock { Text = "输入地块编号，按 Enter 检索", Style = UiTheme.CaptionStyle }); _inputs.Children.Add(_query);
            _inputs.Children.Add(_exact);
            _inputs.Children.Add(_selectedOnly);
            var actions = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 0) };
            var search = new System.Windows.Controls.Button { Content = "查找地块", Padding = new Thickness(12, 5, 12, 5) };
            UiTheme.Primary(search);
            var refresh = new System.Windows.Controls.Button { Content = "刷新当前地图", Margin = new Thickness(10, 0, 0, 0), Padding = new Thickness(12, 5, 12, 5) };
            var scope = new System.Windows.Controls.Button { Content = "加载所选图层", Margin = new Thickness(8, 0, 0, 0), Padding = new Thickness(12, 5, 12, 5) };
            scope.Click += async (_, __) => await Search(true);
            search.Click += async (_, __) => await Search(); refresh.Click += async (_, __) => await LoadLayers();
            actions.Children.Add(search); actions.Children.Add(scope); actions.Children.Add(refresh); _inputs.Children.Add(actions);
            _inputs.Children.Add(new TextBlock { Text = "本次任务问题（从采样助手打开时自动读取）", Margin = new Thickness(0, 8, 0, 0) });
            _inputs.Children.Add(_issues);
            var issue = new System.Windows.Controls.Button { Content = "定位所选问题", Padding = new Thickness(12, 5, 12, 5) };
            issue.Click += async (_, __) => await LocateIssue(); _inputs.Children.Add(issue);
            DockPanel.SetDock(_inputs, Dock.Top); root.Children.Add(_inputs);
            var columns = new GridView();
            columns.Columns.Add(new GridViewColumn { Header = "图层与来源", Width = 360, DisplayMemberBinding = new System.Windows.Data.Binding("LayerName") });
            columns.Columns.Add(new GridViewColumn { Header = "地块编号 / 对象ID", Width = 130, DisplayMemberBinding = new System.Windows.Data.Binding("DisplayId") });
            columns.Columns.Add(new GridViewColumn { Header = "OID", Width = 65, DisplayMemberBinding = new System.Windows.Data.Binding("Oid") });
            _list.View = columns; _list.ItemsSource = _hits; root.Children.Add(_list); Content = root;
            _list.MouseDoubleClick += async (_, __) => await Locate();
            _query.KeyDown += async (_, e) => { if (e.Key == Key.Enter) { e.Handled = true; await Search(); } };
            _layers.SelectionChanged += async (_, __) => { if (!_loading) { _hits.Clear(); await LoadFields(); } };
            Loaded += async (_, __) => await LoadLayers();
        }

        private async Task LoadLayers()
        {
            try
            {
                _inputs.IsEnabled = false;
                _loading = true; _hits.Clear(); _map = MapView.Active?.Map;
                if (_map == null) { _status.Text = "请先打开地图视图，再点击刷新。"; return; }
                _available = await QueuedTask.Run(() => ScopedLayers()
                    .Where(l => l.ShapeType == ArcGIS.Core.CIM.esriGeometryType.esriGeometryPolygon).ToList());
                _layers.Items.Clear(); _layers.Items.Add("全部面图层（自动识别编号）");
                var labels = await QueuedTask.Run(() => _available.Select(layer => { using var table = layer.GetTable(); return layer.Name + " — " + table.GetPath(); }).ToList());
                foreach (var label in labels) _layers.Items.Add(label);
                var preferred = await QueuedTask.Run(() => _available.FindIndex(layer => layer.Name == "补充耕地图斑"));
                _layers.SelectedIndex = preferred >= 0 ? preferred + 1 : _available.Count == 1 ? 1 : 0;
                LoadProblems();
                _status.Text = _available.Count == 0 ? "当前地图没有面图层，请先添加地块数据。" : "输入编号后查找；同号结果会全部列出，双击一条定位。";
            }
            catch (Exception ex) { _status.Text = "读取图层失败：" + ex.Message; }
            finally { _loading = false; await LoadFields(); _inputs.IsEnabled = true; }
        }

        private async Task LoadFields()
        {
            try
            {
                _fields.Items.Clear(); _fieldValues.Clear();
                _fields.Items.Add("自动识别（只搜标准编号）"); _fieldValues.Add(null); _fields.SelectedIndex = 0;
                int index = _layers.SelectedIndex - 1;
                var targets = index >= 0 && index < _available.Count ? new List<FeatureLayer> { _available[index] }
                    : _layers.SelectedIndex == 0 ? _available.ToList() : new List<FeatureLayer>();
                if (targets.Count == 0) return;
                var fieldSets = await QueuedTask.Run(() =>
                {
                    var result = new List<(List<string> Names, bool HasObjectId)>();
                    foreach (var layer in targets)
                    {
                        using var table = layer.GetTable(); using var def = table.GetDefinition();
                        var fields = def.GetFields();
                        result.Add((fields.Where(field => IsIdField(field) && field.FieldType != FieldType.OID).Select(field => field.Name).ToList(),
                            fields.Any(field => field.FieldType == FieldType.OID)));
                    }
                    return result;
                });
                var candidates = ParcelNavigation.SearchFieldCoverage(fieldSets.Select(set => set.Names));
                bool allLayers = _layers.SelectedIndex == 0;
                foreach (var candidate in candidates)
                {
                    var label = allLayers ? candidate.Name + "（" + candidate.LayerCount + "/" + targets.Count + "个图层）" : candidate.Name;
                    _fields.Items.Add(label); _fieldValues.Add(candidate.Name);
                }
                if (index >= 0 && index < _available.Count && fieldSets.Any(set => set.HasObjectId))
                {
                    _fields.Items.Add("对象ID（技术定位）"); _fieldValues.Add(ObjectIdSearchToken);
                }
            }
            catch (Exception ex) { _status.Text = "读取编号字段失败：" + ex.Message; }
        }

        private static bool IsIdField(Field f) => f.FieldType == FieldType.String || f.FieldType == FieldType.Integer || f.FieldType == FieldType.SmallInteger || f.FieldType == FieldType.OID;

        private async Task Search(bool loadScope = false)
        {
            if (!_inputs.IsEnabled) return;
            var term = _query.Text.Trim();
            if (term.Length == 0 && !loadScope) { _status.Text = "请输入地块编号，或点击“加载所选图层”。"; return; }
            if (_map == null || MapView.Active?.Map?.URI != _map.URI) { await LoadLayers(); _status.Text = "地图已变化，图层已刷新，请重新查找。"; return; }
            int layerIndex = _layers.SelectedIndex - 1;
            if (loadScope && layerIndex < 0) { _status.Text = "请选择一个面图层，以确定遍历范围。"; return; }
            var targets = layerIndex >= 0 && layerIndex < _available.Count ? new List<FeatureLayer> { _available[layerIndex] } : _available.ToList();
            string selectedField = _fields.SelectedIndex > 0 && _fields.SelectedIndex < _fieldValues.Count ? _fieldValues[_fields.SelectedIndex] : null;
            bool objectIdSelected = selectedField == ObjectIdSearchToken;
            string fieldName = objectIdSelected ? null : selectedField;
            bool exact = _exact.IsChecked == true;
            bool selectedOnly = _selectedOnly.IsChecked == true;
            _inputs.IsEnabled = false; _hits.Clear(); _status.Text = "正在检索地块……";
            try
            {
                bool limited = false;
                var missingIdFields = new List<string>();
                var hits = await QueuedTask.Run(() =>
                {
                    var found = new List<Hit>(); int scanned = 0;
                    foreach (var layer in targets)
                    {
                        using var table = layer.GetTable(); using var def = table.GetDefinition();
                        var fields = def.GetFields();
                        var automatic = ParcelNavigation.SearchField(layer.Name, fields.Select(item => item.Name));
                        var resolvedFieldName = ParcelNavigation.SearchFieldForLayer(fieldName, automatic, fields.Select(item => item.Name));
                        var field = objectIdSelected ? fields.FirstOrDefault(f => f.FieldType == FieldType.OID) :
                            resolvedFieldName != null ? fields.FirstOrDefault(f => f.Name == resolvedFieldName) : null;
                        bool useObjectId = objectIdSelected && field != null;
                        if (field == null && loadScope && fieldName == null && !objectIdSelected)
                        {
                            field = fields.FirstOrDefault(f => f.FieldType == FieldType.OID);
                            useObjectId = field != null;
                        }
                        if (field == null) { missingIdFields.Add(layer.Name); continue; }
                        using var selection = layer.GetSelection();
                        var selectedIds = ParcelNavigation.SelectionIds(selectedOnly, selection.GetObjectIDs());
                        var filter = new QueryFilter { SubFields = "*" };
                        if (selectedIds != null) filter.ObjectIDs = selectedIds;
                        if (exact && !loadScope)
                        {
                            if (field.FieldType == FieldType.String) filter.WhereClause = field.Name + " = '" + term.Replace("'", "''") + "'";
                            else if (long.TryParse(term, NumberStyles.Integer, CultureInfo.InvariantCulture, out long id)) filter.WhereClause = field.Name + " = " + id.ToString(CultureInfo.InvariantCulture);
                            else continue;
                        }
                        using var cursor = layer.Search(filter);
                        while (cursor.MoveNext())
                        {
                            using var row = cursor.Current;
                            string value = Convert.ToString(row[field.Name], CultureInfo.InvariantCulture) ?? "";
                            if (loadScope || exact || value.IndexOf(term, StringComparison.OrdinalIgnoreCase) >= 0)
                            {
                                long objectId = row.GetObjectID();
                                found.Add(new Hit { Layer = layer, LayerName = layer.Name + " — " + table.GetPath(),
                                    ParcelId = useObjectId ? "" : value, IsObjectId = useObjectId, Oid = objectId,
                                    Reference = Reference(row, fields, layer.URI) });
                            }
                            if (found.Count >= (loadScope ? 10000 : 100) || ++scanned >= 100000) { limited = true; return found; }
                        }
                    }
                    return found;
                });
                foreach (var hit in hits) _hits.Add(hit);
                _status.Text = hits.Count == 0 ? "未找到该编号。请检查图层、字段及定义查询，或取消精确匹配后检索。" : "找到 " + hits.Count + " 条。选择结果后定位。";
                if (missingIdFields.Count > 0)
                    _status.Text += " 未用于本次检索的图层（" + ParcelNavigation.SearchFieldMissingReason(fieldName, objectIdSelected) + "）:" + string.Join("、", missingIdFields.Distinct().Take(5))
                        + "；可在字段列表选择业务编号。对象ID仅在选择单个图层时提供。";
                if (limited) _status.Text += " 已达到本次显示/扫描上限，请缩小范围。";
                if (loadScope) _status.Text += " 本次结果清单已固定；范围按所选图层的定义查询" + (selectedOnly ? "与执行时选择集" : "") + "确定，上一块/下一块只在清单内翻查。";
                if (hits.Count > 0) _list.SelectedIndex = 0;
            }
            catch (Exception ex) { _status.Text = "检索失败：" + ex.Message; }
            finally { _inputs.IsEnabled = true; }
            if (_hits.Count == 1) await Locate();
        }

        private async Task Locate()
        {
            if (!_inputs.IsEnabled) return;
            if (!(_list.SelectedItem is Hit hit)) { _status.Text = "请先查找并选择一条结果。"; return; }
            var view = MapView.Active;
            if (view == null || view.Map.URI != _map?.URI) { _status.Text = "请切回检索时的地图，或刷新后重新查找。"; return; }
            if (!_navigationGate.TryEnter()) return;
            _inputs.IsEnabled = false; _list.IsEnabled = false;
            try
            {
                await QueuedTask.Run(() =>
                {
                    if (!ScopedLayers().Any(layer => layer.URI == hit.Layer.URI)) throw new InvalidOperationException("原图层已变化，请从向导重新打开定位窗口。");
                    using var table = hit.Layer.GetTable(); using var definition = table.GetDefinition();
                    using var cursor = hit.Layer.Search(new QueryFilter { ObjectIDs = new List<long> { hit.Oid }, SubFields = "*" });
                    if (!cursor.MoveNext()) throw new InvalidOperationException("对象不在当前查询范围，请刷新。");
                    using var row = cursor.Current;
                    if (!ParcelNavigation.Matches(hit.Reference, Reference(row, definition.GetFields(), hit.Layer.URI)))
                        throw new InvalidOperationException("对象编号或项目已改变，请刷新后重新定位。");
                    hit.Layer.SetVisibility(true); view.Map.ClearSelection();
                    using var selection = hit.Layer.Select(new QueryFilter { ObjectIDs = new List<long> { hit.Oid } }, SelectionCombinationMethod.New);
                    if (selection.GetCount() != 1) throw new InvalidOperationException("地块已变化，请刷新后重新查询。");
                });
                if (!await view.ZoomToSelectedAsync(TimeSpan.FromSeconds(0.6))) throw new InvalidOperationException("无法缩放到该地块，请检查几何和地图坐标系。");
                var partition = ParcelNavigation.ForNavigation(_hits.Select(item => item.Reference), hit.Reference);
                _status.Text = "第 " + (partition.FindIndex(item => item.Oid == hit.Oid) + 1) + "/" + partition.Count + " 个；已定位：" + hit.LayerName + " / " + hit.DisplayId + "。";
                if (string.IsNullOrEmpty(hit.Reference.ProjectId) || hit.Reference.ProjectId == "未填")
                    _status.Text += " 项目归属未核实，前后翻查仅限当前单元。";
            }
            catch (Exception ex) { _status.Text = "定位失败，请刷新后重试：" + ex.Message; }
            finally { _list.IsEnabled = true; _inputs.IsEnabled = true; _navigationGate.Exit(); }
        }

        private IEnumerable<FeatureLayer> ScopedLayers()
        {
            if (_map == null) return Enumerable.Empty<FeatureLayer>();
            if (string.IsNullOrEmpty(_groupUri)) return _map.GetLayersAsFlattenedList().OfType<FeatureLayer>();
            var group = _map.GetLayersAsFlattenedList().OfType<GroupLayer>().FirstOrDefault(layer => layer.URI == _groupUri);
            if (group == null) throw new InvalidOperationException("本次预览图层已经更新，请从采样助手重新打开定位窗口。");
            return group.GetLayersAsFlattenedList().OfType<FeatureLayer>();
        }

        private ParcelReference Reference(Row row, IEnumerable<Field> fields, string uri)
        {
            var names = fields.Select(field => field.Name).ToList();
            string Value(params string[] options)
            {
                var field = options.Select(option => names.FirstOrDefault(name => name.Equals(option, StringComparison.OrdinalIgnoreCase)))
                    .FirstOrDefault(name => name != null);
                return field == null ? "" : Convert.ToString(row[field], CultureInfo.InvariantCulture)?.Trim() ?? "";
            }
            var result = new ParcelReference { Oid = row.GetObjectID(), LayerUri = uri,
                ParcelId = Value("DIKUAI_NO", "DKBH", "DKBM", "BSM", "PL_NAME"), UnitId = Value("PJDYBH", "PJDYBH_M", "DYBH", "EVAL_UNIT"),
                PointId = Value("POINT_ID", "CYDBH"), SampleId = Value("SAMPLE_ID"), ReplicateNo = Value("REP_NO"),
                ProjectId = Value("XMBH", "PROJECT_ID", "XM_ID") };
            if (string.IsNullOrEmpty(result.ProjectId) && _unitProjects.TryGetValue(result.UnitId, out var project)) result.ProjectId = project;
            if ((string.IsNullOrEmpty(result.ProjectId) || result.ProjectId == "未填") &&
                System.Text.RegularExpressions.Regex.IsMatch(result.UnitId, "^[A-Z][0-9]{14}E[0-9]{4}$")) result.ProjectId = result.UnitId.Substring(0, 15);
            return result;
        }

        private async Task Navigate(int direction)
        {
            if (!_inputs.IsEnabled) return;
            if (_hits.Count == 0) await Search(true);
            if (_hits.Count == 0) return;
            var selected = _list.SelectedItem as Hit ?? _hits[0];
            var partition = ParcelNavigation.ForNavigation(_hits.Select(item => item.Reference), selected.Reference);
            var index = partition.FindIndex(item => item.Oid == selected.Oid);
            var next = ParcelNavigation.NextIndex(index, direction, partition.Count);
            if (next < 0) return;
            _list.SelectedItem = _hits.First(item => item.Oid == partition[next].Oid && item.Reference.LayerUri == selected.Reference.LayerUri);
            _list.ScrollIntoView(_list.SelectedItem);
            await Locate();
        }

        private static string Digest(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path)));

        private void LoadProblems()
        {
            _issues.Items.Clear(); _unitProjects.Clear(); _reportDigest = null;
            if (string.IsNullOrEmpty(_reportPath)) return;
            if (!File.Exists(_reportPath)) throw new InvalidOperationException("本次任务报告不存在，请重新准备预览。");
            using var document = JsonDocument.Parse(File.ReadAllText(_reportPath));
            var report = document.RootElement;
            if (report.TryGetProperty("单元明细", out var units))
                foreach (var unit in units.EnumerateArray())
                    if (unit.TryGetProperty("unit", out var id) && unit.TryGetProperty("project", out var project))
                        _unitProjects[id.ToString()] = project.ToString();
            var problems = ParcelNavigation.ReadReport(report);
            if (report.TryGetProperty("独立采样质检", out var sampling) && sampling.TryGetProperty("path", out var path))
            {
                var qaPath = Path.GetFullPath(path.GetString());
                var permitted = Path.GetFullPath(_taskRoot ?? Path.GetDirectoryName(_reportPath)).TrimEnd('\\', '/') + Path.DirectorySeparatorChar;
                if (!qaPath.StartsWith(permitted, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("检查报告引用超出本任务目录，拒绝读取。");
                using var qa = JsonDocument.Parse(File.ReadAllText(qaPath));
                problems.AddRange(ParcelNavigation.ReadQa(qa.RootElement, "采样质检"));
            }
            foreach (var problem in problems.Where(item => !string.IsNullOrEmpty(item.UnitId) || !string.IsNullOrEmpty(item.ParcelId) || !string.IsNullOrEmpty(item.PointId)))
                _issues.Items.Add(problem);
            if (_issues.Items.Count > 0) _issues.SelectedIndex = 0;
            _reportDigest = Digest(_reportPath);
        }

        private async Task LocateIssue()
        {
            if (!_inputs.IsEnabled) return;
            if (!(_issues.SelectedItem is ParcelReference issue)) { _status.Text = "未选择可定位的问题；请从采样助手打开，并查看报告中的一般说明。"; return; }
            if (_map == null || MapView.Active?.Map?.URI != _map.URI) { _status.Text = "地图已切换，请重新从向导打开定位窗口。"; return; }
            _inputs.IsEnabled = false; _hits.Clear();
            try
            {
                if (Digest(_reportPath) != _reportDigest) throw new InvalidOperationException("任务报告已更新，请刷新问题列表。");
                if (!string.IsNullOrEmpty(_taskRoot))
                {
                    using var state = JsonDocument.Parse(File.ReadAllText(Path.Combine(_taskRoot, "task_state.json")));
                    using var report = JsonDocument.Parse(File.ReadAllText(_reportPath));
                    if (state.RootElement.TryGetProperty("design_revision", out var current) && report.RootElement.TryGetProperty("设计修订号", out var expected)
                        && current.GetInt32() != expected.GetInt32()) throw new InvalidOperationException("设计已修改，请重新准备并刷新问题列表。");
                }
                var hits = await QueuedTask.Run(() =>
                {
                    var result = new List<Hit>();
                    var layers = ScopedLayers().ToList();
                    bool point = !string.IsNullOrEmpty(issue.PointId) || !string.IsNullOrEmpty(issue.SampleId) || !string.IsNullOrEmpty(issue.ReplicateNo);
                    if (!point)
                    {
                        layers = layers.Where(layer => layer.ShapeType == ArcGIS.Core.CIM.esriGeometryType.esriGeometryPolygon).ToList();
                        if (string.IsNullOrEmpty(issue.ParcelId) && layers.Any(layer => layer.Name == "评价单元"))
                            layers = layers.Where(layer => layer.Name == "评价单元").ToList();
                    }
                    int scanned = 0;
                    foreach (var layer in layers)
                    {
                        using var table = layer.GetTable(); using var definition = table.GetDefinition();
                        var fields = definition.GetFields(); var names = fields.Select(field => field.Name.ToUpperInvariant()).ToList();
                        if (!ParcelNavigation.HasIssueFields(issue, names)) continue;
                        using var cursor = layer.Search(new QueryFilter { SubFields = "*" });
                        while (cursor.MoveNext())
                        {
                            using var row = cursor.Current;
                            if (++scanned > 100000) throw new InvalidOperationException("问题定位达到扫描上限，请先缩小地图查询范围。");
                            var reference = Reference(row, fields, layer.URI);
                            if (!ParcelNavigation.Matches(issue, reference)) continue;
                            result.Add(new Hit { Layer = layer, LayerName = layer.Name + " — " + table.GetPath(), Oid = row.GetObjectID(),
                                ParcelId = !string.IsNullOrEmpty(reference.PointId) ? reference.PointId : !string.IsNullOrEmpty(reference.ParcelId) ? reference.ParcelId : reference.UnitId,
                                Reference = reference });
                            if (result.Count > 100) throw new InvalidOperationException("同号匹配超过100个，请先核对项目和编号，未自动跳转。");
                        }
                    }
                    return result;
                });
                foreach (var hit in hits) _hits.Add(hit);
                if (hits.Count > 0) _list.SelectedIndex = 0;
                _status.Text = hits.Count == 0 ? "该问题编号不在本次图层/查询范围，或编号已经改变；请检查报告。" : "匹配 " + hits.Count + " 个对象；多结果请选择完整来源后定位。";
            }
            catch (Exception ex) { _status.Text = "问题定位失败：" + ex.Message; }
            finally { _inputs.IsEnabled = true; }
            if (_hits.Count == 1) await Locate();
        }

        private sealed class Hit
        {
            internal FeatureLayer Layer { get; set; }
            public string LayerName { get; set; }
            public string ParcelId { get; set; }
            public long Oid { get; set; }
            public bool IsObjectId { get; set; }
            public string DisplayId => IsObjectId ? "对象ID " + Oid.ToString(CultureInfo.InvariantCulture) : ParcelId;
            internal ParcelReference Reference { get; set; }
        }
    }
}
