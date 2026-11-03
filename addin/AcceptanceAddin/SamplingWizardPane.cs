using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
// ArcGIS.Desktop.Framework.Contracts 里也有 Button/ComboBox/CheckBox，必须显式消歧
using Button = System.Windows.Controls.Button;
using CheckBox = System.Windows.Controls.CheckBox;
using ComboBox = System.Windows.Controls.ComboBox;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Events;
using AcceptanceAddin.Ui;
using UiKit = AcceptanceAddin.Ui.Ui;

namespace AcceptanceAddin
{
    /// <summary>
    /// 采样助手侧栏：四步填完就能出成果。所有重活都交给 GridSampling.pyt，
    /// 面板只负责取值、校验和把 GP 消息翻译成人话，保证面板与工具箱是同一条执行路径。
    /// </summary>
    internal sealed class SamplingWizardPane : UserControl
    {
        internal bool IsBusy => _busy;
        internal int ProjectNameLength => _projectName.Text.Length;
        private bool _initialLoadDone;
        private bool _tdtCredentialDirty, _updatingTdtCredential;
        private string _sourceProjectUri = "", _sourceMapUri = "";
        private string _configuredSource;
        private string _stationOrg = "", _stationDrawnBy = "", _stationReviewer = "";
        private readonly Expander _projectDetails = new Expander { Header = "项目与出图信息" };
        private readonly Expander _settings = new Expander { Header = "设置" };
        private readonly Expander _delivery = new Expander { Header = "确认并导出", Visibility = Visibility.Collapsed };
        private readonly StackPanel _mapResults = new StackPanel { Visibility = Visibility.Collapsed };
        private readonly StackPanel _localImageryOptions = new StackPanel();
        private readonly StackPanel _serviceOptions = new StackPanel();
        private readonly StackPanel _credentialOptions = new StackPanel();
        private readonly StackPanel _tiandituOptions = new StackPanel();
        private readonly TextBlock _emptyPreview = new TextBlock { Text = "选择图斑后生成预览。", Margin = new Thickness(0, 12, 0, 8) };
        private bool _refreshingMap, _mapRefreshPending, _updatingLayers;
        private ParcelEditingPanel _parcelEditor;
        private readonly ComboBox _mapPreviewChoices = new ComboBox { Margin = new Thickness(0, 4, 0, 6) };
        private readonly Button _reviewMaps = new Button { Content = "AI检查整套图件", Margin = new Thickness(0, 0, 0, 6), IsEnabled = false };
        private System.Threading.CancellationTokenSource _visualReviewCancellation;
        private readonly System.Windows.Controls.Image _mapPreview = new System.Windows.Controls.Image
            { MaxHeight = 340, Stretch = Stretch.Uniform, Margin = new Thickness(0, 0, 0, 8), Cursor = System.Windows.Input.Cursors.Hand };
        private WorkbenchJobService.JobLease _samplingJob;
        private bool _samplingCanStop;
        private JsonObject _receipt, _diagnostic;
        private string[] _diagnosticSecrets = Array.Empty<string>();
        private string _lastTaskRoot, _lastSourcePath, _lastProjectUri;
        private readonly Dictionary<string, double> _mapScales = new Dictionary<string, double>();
        private readonly ComboBox _mapScale = new ComboBox { IsEditable = true, MinWidth = 120, Margin = new Thickness(4) };
        private readonly Button _applyMapScale = new Button { Content = "应用比例尺", Margin = new Thickness(4) };
        private readonly Button _redrawSelected = new Button { Content = "重绘当前图", Margin = new Thickness(4) };
        private readonly Button _redrawAll = new Button { Content = "重绘整套图", Margin = new Thickness(4) };
        private bool _localBusy;
        private readonly TextBlock _phase = new TextBlock { TextWrapping = TextWrapping.Wrap };
        private readonly Button _doctor = new Button { Content = "环境检查" };
        private readonly Button _copyDiagnostic = new Button { Content = "复制诊断" };
        internal SamplingWizardPane()
        {
            Content = CreateContent();
            LayersAddedEvent.Subscribe(_ => QueueMapRefresh());
            LayersRemovedEvent.Subscribe(_ => QueueMapRefresh());
            ActiveMapViewChangedEvent.Subscribe(_ => QueueMapRefresh());
            WorkbenchJobService.Changed += () => Dispatcher.InvokeAsync(RefreshActions);
            _reviewer.TextChanged += (_, __) => RefreshActions();
            _designConfirmed.Checked += (_, __) => RefreshActions();
            _designConfirmed.Unchecked += (_, __) => RefreshActions();
            RefreshActions();
        }

        private readonly ComboBox _layers = new ComboBox { Margin = new Thickness(0, 2, 0, 6) };
        private readonly ComboBox _scope = new ComboBox { Margin = new Thickness(0, 2, 0, 6) };
        private readonly ComboBox _mapImagery = new ComboBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly TextBox _serviceConfig = new TextBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly System.Windows.Controls.Image _onlinePreview = new System.Windows.Controls.Image
            { MaxHeight = 260, Stretch = Stretch.Uniform, Margin = new Thickness(0, 6, 0, 4) };
        private readonly Dictionary<string, ComboBox> _factorChoices = new Dictionary<string, ComboBox>();
        private readonly Dictionary<string, ComboBox> _unitImageryChoices = new Dictionary<string, ComboBox>();
        private readonly StackPanel _unitImageryPanel = new StackPanel();
        private readonly TextBox _projectNumber = new TextBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly TextBox _projectName = new TextBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly TextBox _county = new TextBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly TextBox _town = new TextBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly TextBox _village = new TextBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly ComboBox _idField = new ComboBox { Margin = new Thickness(0, 2, 0, 6) };
        private readonly Button _saveParams = new Button { Content = "保存项目参数…", Padding = new Thickness(8, 3, 8, 3) };
        private readonly Button _loadParams = new Button { Content = "加载项目参数…", Padding = new Thickness(8, 3, 8, 3) };
        private readonly Button _summary = new Button { Content = "打开本次运行摘要", Padding = new Thickness(10, 4, 10, 4), Margin = new Thickness(0, 0, 0, 6), IsEnabled = false };
        private readonly TextBox _reviewer = new TextBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly CheckBox _designConfirmed = new CheckBox { Content = "已在地图核对本次单元与设计点（不代表现场已采样）", Margin = new Thickness(0, 4, 0, 4) };
        private readonly Button _prepare = new Button { Content = "一键生成预览图", FontWeight = FontWeights.Bold, Margin = new Thickness(0, 4, 0, 4), Padding = new Thickness(10, 8, 10, 8) };
        private string _previewGroupUri;
        private string _previewTaskRoot;
        private string _previewFingerprint;
        private string _previewSignature;
        private int _previewRevision;
        private readonly ComboBox _pointIds = new ComboBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly TextBlock _layerHint = new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            Foreground = new SolidColorBrush(Color.FromRgb(0x55, 0x55, 0x55)),
            Margin = new Thickness(0, 0, 0, 8)
        };
        private readonly TextBox _cell = Num("");
        private readonly TextBox _minPoints = Num("3");
        private readonly TextBox _spacing = Num("15");
        private readonly TextBox _inset = Num("5");
        private readonly TextBox _maxMu = Num("50");
        private readonly TextBox _gap = Num("30");
        private readonly CheckBox _multipart = new CheckBox
        {
            Content = "允许把不共边的图斑合并为一个单元",
            IsChecked = false,
            Margin = new Thickness(0, 2, 0, 6)
        };
        private readonly ComboBox _imageryKind = new ComboBox { Margin = new Thickness(0, 2, 0, 4) };
        private int _credentialKind = -1;
        private readonly TextBox _imageryPath = new TextBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly PasswordBox _tiandituKey = new PasswordBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly TextBox _outDir = new TextBox { Margin = new Thickness(0, 2, 0, 4) };
        private readonly TextBox _org = new TextBox { Margin = new Thickness(0, 2, 0, 4), Text = "" };
        private readonly TextBox _sheetNo = new TextBox { Margin = new Thickness(0, 2, 0, 4), Text = "" };
        private readonly TextBox _drawnBy = new TextBox { Margin = new Thickness(0, 2, 0, 4), Text = "" };
        private readonly Button _run = new Button
        {
            Content = "一键生成成果并出图",
            Padding = new Thickness(10, 8, 10, 8),
            FontWeight = FontWeights.Bold,
            Margin = new Thickness(0, 8, 0, 6)
        };
        private readonly Button _open = new Button
        {
            Content = "打开成果文件夹",
            Padding = new Thickness(10, 4, 10, 4),
            Margin = new Thickness(0, 0, 0, 6),
            IsEnabled = false
        };
        private readonly Button _check = new Button { Content = "检查资料", Margin = new Thickness(0, 4, 0, 4) };
        private readonly Button _resume = new Button { Content = "继续上次任务", Margin = new Thickness(0, 4, 0, 4), IsEnabled = false };
        private readonly Button _cancel = new Button { Content = "取消（当前阶段完成后停止）", Margin = new Thickness(0, 4, 0, 4), IsEnabled = false };
        private readonly TextBlock _status = new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 2, 0, 6),
            Foreground = new SolidColorBrush(Color.FromRgb(0x1B, 0x5E, 0x20))
        };
        private readonly TextBox _log = new TextBox
        {
            IsReadOnly = true,
            AcceptsReturn = true,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Height = 120,
            Margin = new Thickness(0, 2, 0, 0)
        };
        private List<LayerChoice> _layerChoices = new List<LayerChoice>();
        private bool _busy { get => _localBusy || WorkbenchJobService.IsBusy; set => _localBusy = value; }
        private bool _previewCanExport;
        private bool _metadataDirty = true;

        private sealed class LayerChoice
        {
            public string Name { get; }
            public string Path { get; }
            public long FeatureCount { get; }
            public List<long> SelectedObjectIds { get; }
            public string DefinitionWhere { get; }
            public long QueryCount { get; }
            public string LayerUri { get; }
            public bool FromFolder { get; }
            public bool AutoImageryCandidate { get; set; }
            public List<string> Fields { get; set; } = new List<string>();

            public LayerChoice(string name, string path, long featureCount,
                List<long> selectedObjectIds, string definitionWhere, long queryCount,
                string layerUri = null, bool fromFolder = false)
            {
                Name = name;
                Path = ProAiBridge.PaneState.DatasetPath(path);
                FeatureCount = featureCount;
                SelectedObjectIds = selectedObjectIds ?? new List<long>();
                DefinitionWhere = definitionWhere;
                QueryCount = queryCount;
                LayerUri = layerUri;
                FromFolder = fromFolder;
            }

            public override string ToString() => Name + "（" + FeatureCount + " 个）";
        }

        private sealed class ScopeChoice
        {
            public string Mode { get; }
            private readonly string _label;
            public ScopeChoice(string mode, string label) { Mode = mode; _label = label; }
            public override string ToString() => _label;
        }

        private sealed class FactorChoice
        {
            public string Path { get; }
            public string Field { get; }
            public FactorChoice(string path, string field) { Path = path; Field = field; }
            public override string ToString() => Path == null ? "使用图斑现有字段；缺项保留待补" : Field + " — " + Path;
        }

        private sealed class MapPreviewChoice
        {
            internal string TargetName { get; set; }
            internal string ScaleKey { get; set; }
            internal double Scale { get; set; }
            internal string Title { get; set; }
            internal string Path { get; set; }
            internal string Kind { get; set; }
            internal string RenderKey { get; set; }
            internal string ReviewContext { get; set; }
            public override string ToString() => Scale > 0 ? Title + " · 1:" + Scale.ToString(System.Globalization.CultureInfo.InvariantCulture) : Title;
        }

        private Control CreateContent()
        {
            UiTheme.Apply(this);
            LoadStationDefaults();
            System.Windows.Automation.AutomationProperties.SetName(_layers, "补充耕地图斑图层");
            System.Windows.Automation.AutomationProperties.SetName(_scope, "采样处理范围");
            System.Windows.Automation.AutomationProperties.SetName(_idField, "图斑编号字段");
            System.Windows.Automation.AutomationProperties.SetName(_projectNumber, "备案项目编号");
            System.Windows.Automation.AutomationProperties.SetName(_projectName, "项目名称");
            System.Windows.Automation.AutomationProperties.SetName(_outDir, "成果输出目录");
            System.Windows.Automation.AutomationProperties.SetName(_cell, "网格边长米");
            System.Windows.Automation.AutomationProperties.SetName(_minPoints, "每组最少设计点数");
            System.Windows.Automation.AutomationProperties.SetName(_tiandituKey, "采样影像服务Key");
            System.Windows.Automation.AutomationProperties.SetName(_mapPreviewChoices, "预览图件");
            System.Windows.Automation.AutomationProperties.SetName(_reviewer, "设计确认人");
            var root = new StackPanel { Margin = new Thickness(0, 0, 0, 12) };
            root.Children.Add(Header("图斑"));
            root.Children.Add(_layers);
            _layers.SelectionChanged += async (_, __) =>
            { if (_updatingLayers) return; ResetPreviewBinding(true); if (_parcelEditor != null) await _parcelEditor.SourceChanged();
                SyncScope(); SyncIdFields(); AutoConfigureSource(); await StyleSourceMap(); };
            _scope.SelectionChanged += (_, __) => { if (!_updatingLayers) ResetPreviewBinding(true); };
            root.Children.Add(_scope);
            root.Children.Add(_layerHint);
            var sourceActions = new WrapPanel();
            var refresh = new Button { Content = "刷新地图资料", Margin = new Thickness(0, 0, 6, 4) };
            refresh.Click += async (_, __) => await LoadLayers();
            var discover = new Button { Content = "选择资料文件夹", Margin = new Thickness(0, 0, 0, 4) };
            discover.Click += async (_, __) => await DiscoverFolder();
            sourceActions.Children.Add(refresh); sourceActions.Children.Add(discover);
            root.Children.Add(new Expander { Header = "更换资料", Content = sourceActions });
            _parcelEditor = new ParcelEditingPanel(ParcelEditSource, UseEditedParcels);
            root.Children.Add(new Expander { Header = "编辑图斑", Content = _parcelEditor });

            var imageryPanel = new StackPanel();
            var fieldPanel = new StackPanel();
            fieldPanel.Children.Add(Hint("图斑编号字段"));
            _idField.Items.Add("自动识别（推荐）"); _idField.SelectedIndex = 0;
            _idField.SelectionChanged += (_, __) => ResetPreviewBinding();
            fieldPanel.Children.Add(_idField);
            foreach (var role in new Dictionary<string, string> { { "landform", "地形部位" }, { "soil_species", "土种" },
                { "supplement_source", "补充耕地来源" }, { "land_use", "土地利用现状" } })
            {
                fieldPanel.Children.Add(Hint(role.Value));
                var selector = new ComboBox { Margin = new Thickness(0, 2, 0, 4) };
                selector.Items.Add(new FactorChoice(null, null)); selector.SelectedIndex = 0;
                selector.SelectionChanged += (_, __) => ResetPreviewBinding();
                _factorChoices[role.Key] = selector; fieldPanel.Children.Add(selector);
            }
            imageryPanel.Children.Add(Hint("影像来源"));
            var currentImagery = new Button { Content = "重新读取地图影像", Margin = new Thickness(0, 4, 0, 4) };
            currentImagery.Click += async (_, __) => await LoadCurrentImagery();
            imageryPanel.Children.Add(currentImagery); imageryPanel.Children.Add(_mapImagery);
            _mapImagery.SelectionChanged += (_, __) =>
            {
                if (_updatingLayers) return;
                if (_mapImagery.SelectedItem is LayerChoice image)
                {
                    if (IsOnlineSource(image.Path)) { _imageryKind.SelectedIndex = 3; _serviceConfig.Text = image.Path; }
                    else { _imageryKind.SelectedIndex = 1; _imageryPath.Text = image.Path; }
                }
            };
            _imageryKind.Items.Add("Esri影像（测试预览）");
            _imageryKind.Items.Add("本地DOM或影像文件夹");
            _imageryKind.Items.Add("天地图影像（已存密钥自动使用）");
            _imageryKind.Items.Add("单位授权在线影像服务");
            _imageryKind.SelectedIndex = 0;
            _imageryKind.SelectionChanged += (_, __) =>
            {
                var kind = _imageryKind.SelectedIndex;
                if (kind != _credentialKind)
                {
                    _updatingTdtCredential = true; _tiandituKey.Clear();
                    if (kind == 2) { try { _tiandituKey.Password = ServiceCredentials.Load() ?? ""; } catch { } }
                    _credentialKind = kind; _tdtCredentialDirty = false; _updatingTdtCredential = false;
                }
                SyncImagery(); ResetPreviewBinding();
            };
            imageryPanel.Children.Add(_imageryKind);
            _localImageryOptions.Children.Add(Hint("本地影像路径")); _localImageryOptions.Children.Add(_imageryPath);
            _imageryPath.TextChanged += (_, __) => { if (!_busy) ResetPreviewBinding(); };
            var browseImage = new Button { Content = "选影像文件", Margin = new Thickness(0, 2, 0, 2) };
            browseImage.Click += (_, __) => BrowseFile(_imageryPath); _localImageryOptions.Children.Add(browseImage);
            var browseImageFolder = new Button { Content = "选影像文件夹", Margin = new Thickness(0, 2, 0, 4) };
            browseImageFolder.Click += (_, __) => BrowseFolder(_imageryPath); _localImageryOptions.Children.Add(browseImageFolder);
            imageryPanel.Children.Add(_localImageryOptions);
            _serviceOptions.Children.Add(Hint("服务地址或配置JSON")); _serviceOptions.Children.Add(_serviceConfig);
            var browseService = new Button { Content = "选服务配置", Margin = new Thickness(0, 2, 0, 4) };
            browseService.Click += (_, __) => BrowseFile(_serviceConfig); _serviceOptions.Children.Add(browseService);
            imageryPanel.Children.Add(_serviceOptions);
            _serviceConfig.TextChanged += (_, __) => { if (!_busy) { if (_imageryKind.SelectedIndex == 3) _tiandituKey.Clear(); ResetPreviewBinding(); } };
            _credentialOptions.Children.Add(Hint("服务密钥")); _credentialOptions.Children.Add(_tiandituKey);
            imageryPanel.Children.Add(_credentialOptions);
            _tiandituKey.PasswordChanged += (_, __) =>
            {
                if (!_updatingTdtCredential && _imageryKind.SelectedIndex == 2) _tdtCredentialDirty = true;
                if (!_busy) ResetPreviewBinding();
            };
            var rememberKey = new Button { Content = "保存天地图密钥", Margin = new Thickness(0, 2, 0, 4) };
            rememberKey.Click += (_, __) =>
            {
                if (_busy) return;
                try
                {
                    if (_imageryKind.SelectedIndex != 2) throw new InvalidOperationException("请选择天地图后保存对应密钥。");
                    ServiceCredentials.Save(_tiandituKey.Password); _tdtCredentialDirty = false; _status.Text = "天地图密钥已加密保存。";
                }
                catch (Exception ex) { _status.Text = "密钥未保存：" + ex.Message; }
            };
            _tiandituOptions.Children.Add(rememberKey); imageryPanel.Children.Add(_tiandituOptions);
            try { if (!string.IsNullOrWhiteSpace(ServiceCredentials.Load())) _imageryKind.SelectedIndex = 2; }
            catch (Exception ex) { _status.Text = "底图凭证读取失败：" + ex.Message; }
            var previewOnline = new Button { Content = "只检查底图连接", Margin = new Thickness(0, 2, 0, 4) };
            previewOnline.Click += async (_, __) => await PreviewOnline();
            imageryPanel.Children.Add(previewOnline); imageryPanel.Children.Add(_onlinePreview);
            var settingsPanel = new StackPanel();
            settingsPanel.Children.Add(new Expander { Header = "影像来源", Content = imageryPanel });
            settingsPanel.Children.Add(new Expander { Header = "字段与辅助资料", Content = fieldPanel });

            var projectPanel = new StackPanel();
            projectPanel.Children.Add(Hint("备案项目编号（未取得可留空）")); projectPanel.Children.Add(_projectNumber);
            projectPanel.Children.Add(Hint("项目名称")); projectPanel.Children.Add(_projectName);
            projectPanel.Children.Add(Grid2("县（市、区）", _county, "乡（镇、街办）", _town));
            projectPanel.Children.Add(Hint("村")); projectPanel.Children.Add(_village);
            projectPanel.Children.Add(Hint("图号（可留空）")); projectPanel.Children.Add(_sheetNo);
            projectPanel.Children.Add(Hint("成果目录（自动创建新任务）")); projectPanel.Children.Add(_outDir);
            var browseOut = new Button { Content = "更改成果目录", Margin = new Thickness(0, 2, 0, 4) };
            browseOut.Click += (_, __) => BrowseFolder(_outDir); projectPanel.Children.Add(browseOut);
            var paramsRow = new WrapPanel(); paramsRow.Children.Add(_saveParams); paramsRow.Children.Add(_loadParams);
            projectPanel.Children.Add(paramsRow);
            var technical = new StackPanel();
            technical.Children.Add(Grid2("网格米（空=自动）", _cell, "每组最低点数", _minPoints));
            technical.Children.Add(Grid2("通常间距米", _spacing, "避边建议米", _inset));
            technical.Children.Add(Grid2("单元上限亩", _maxMu, "相邻距离米", _gap)); technical.Children.Add(_multipart);
            _check.Content = "只检查资料"; technical.Children.Add(_check);
            projectPanel.Children.Add(new Expander { Header = "采样技术参数", Content = technical });
            _projectDetails.Content = projectPanel; settingsPanel.Children.Add(_projectDetails);
            _settings.Content = settingsPanel; root.Children.Add(_settings);

            root.Children.Add(_emptyPreview);
            _mapResults.Children.Add(_mapPreviewChoices); _mapResults.Children.Add(_mapPreview);
            foreach (var denominator in new[] { "自动", "1000", "1500", "2000", "2500", "3000", "5000", "10000", "25000", "50000" })
                _mapScale.Items.Add(denominator);
            var mapActions = new WrapPanel();
            mapActions.Children.Add(new TextBlock { Text = "比例尺 1:", VerticalAlignment = VerticalAlignment.Center });
            foreach (var control in new Control[] { _mapScale, _applyMapScale, _redrawSelected, _redrawAll }) mapActions.Children.Add(control);
            var mapOptions = new StackPanel(); mapOptions.Children.Add(mapActions);
            System.Windows.Automation.AutomationProperties.SetName(_mapScale, "当前图幅比例尺分母，自动或正数");
            _applyMapScale.Click += async (_, __) => await ApplyMapScale();
            _redrawSelected.Click += async (_, __) => await RedrawMaps(false);
            _redrawAll.Click += async (_, __) => await RedrawMaps(true);
            mapOptions.Children.Add(_reviewMaps);
            _reviewMaps.Click += async (_, __) =>
            {
                var reviewRoot = _previewTaskRoot ?? (string)_receipt?["root"];
                if (SameTask(reviewRoot, _outDir.Text)) await ReviewMapPreviewsAsync(reviewRoot, automatic: false);
            };
            _mapPreviewChoices.SelectionChanged += (_, __) => ShowMapPreview();
            _mapPreview.MouseLeftButtonUp += (_, __) => OpenSelectedMap();
            var locate = new Button { Content = "按编号定位地块", Margin = new Thickness(0, 4, 0, 4) };
            locate.Click += (_, __) => ParcelLocatorWindow.Open(
                string.IsNullOrEmpty(_previewTaskRoot) ? null : CurrentReportPath(_previewTaskRoot), _previewGroupUri, _previewTaskRoot);
            mapOptions.Children.Add(locate);
            var adjustments = new StackPanel();
            adjustments.Children.Add(_pointIds);
            var adjust = new Button { Content = "在地图点选新位置并吸附", Margin = new Thickness(0, 4, 0, 4) };
            adjust.Click += async (_, __) =>
            {
                if (_busy || _pointIds.SelectedItem == null || string.IsNullOrWhiteSpace(_reviewer.Text) ||
                    _previewTaskRoot == null || !SameTask(_previewTaskRoot, _outDir.Text))
                { _status.Text = "先生成预览，选中点号并填写修改人。"; return; }
                _designConfirmed.IsChecked = false;
                PointAdjustmentTool.TaskRoot = _previewTaskRoot; PointAdjustmentTool.PointId = _pointIds.SelectedItem.ToString();
                PointAdjustmentTool.Operator = _reviewer.Text.Trim(); PointAdjustmentTool.ExpectedSignature = _previewSignature;
                PointAdjustmentTool.ExpectedRevision = _previewRevision;
                await FrameworkApplication.SetCurrentToolAsync("AcceptanceAddin_PointAdjustment");
                _status.Text = "请在地图上点击 " + PointAdjustmentTool.PointId + " 的新位置。";
            };
            adjustments.Children.Add(adjust);
            adjustments.Children.Add(new Expander { Header = "逐单元替换影像", Content = new ScrollViewer
                { Content = _unitImageryPanel, MaxHeight = 240, VerticalScrollBarVisibility = ScrollBarVisibility.Auto } });
            mapOptions.Children.Add(new Expander { Header = "调整点位与影像", Content = adjustments });
            _mapResults.Children.Add(new Expander { Header = "调整图件", Content = mapOptions });
            root.Children.Add(_mapResults);
            var deliveryPanel = new StackPanel();
            deliveryPanel.Children.Add(Hint("编制单位")); deliveryPanel.Children.Add(_org);
            deliveryPanel.Children.Add(Hint("制图人")); deliveryPanel.Children.Add(_drawnBy);
            deliveryPanel.Children.Add(Hint("设计确认人")); deliveryPanel.Children.Add(_reviewer);
            deliveryPanel.Children.Add(_designConfirmed); deliveryPanel.Children.Add(_run);
            _delivery.Content = deliveryPanel; root.Children.Add(_delivery);
            _run.Content = "确认设计并导出";
            UiTheme.Primary(_run);
            UiTheme.Primary(_prepare);
            UiTheme.Quiet(_check); UiTheme.Quiet(_resume);
            _prepare.FontSize = 13;
            _status.Style = UiTheme.CaptionStyle;
            UiTheme.WatchStatus(_status);
            _status.Margin = new Thickness(0, 1, 0, 6);
            _phase.FontSize = 11.5;
            _phase.FontWeight = FontWeights.SemiBold;
            _phase.Margin = new Thickness(0, 0, 0, 2);
            _emptyPreview.Style = UiTheme.CaptionStyle;
            _emptyPreview.Margin = new Thickness(0, 12, 0, 8);
            _layerHint.Foreground = UiTheme.Brush(UiTheme.TextMuted);
            _log.FontFamily = new FontFamily("Cascadia Mono, Consolas");
            _log.FontSize = 10.5;
            _log.Background = UiTheme.Brush(UiTheme.SurfaceAlt);
            _outDir.TextChanged += async (_, __) =>
            {
                ResetPreviewBinding(true);
                _receipt = null; RefreshActions();
                var rootPath = _outDir.Text.Trim();
                var saved = await Task.Run(() => WorkbenchTaskReceipt.Read(rootPath));
                if (!_busy && SameTask(rootPath, _outDir.Text))
                { _receipt = saved; await RestorePreviewImages(rootPath, saved); RefreshActions(); }
            };
            foreach (var box in new[] { _projectNumber, _cell, _minPoints, _spacing, _inset, _maxMu, _gap })
                box.TextChanged += (_, __) => ResetPreviewBinding();
            foreach (var box in new[] { _org, _drawnBy, _sheetNo, _projectName, _county, _town, _village })
                box.TextChanged += (_, __) =>
                {
                    _metadataDirty = true; _previewCanExport = false; _designConfirmed.IsChecked = false;
                    if (_mapPreviewChoices.Items.Count > 0) _status.Text = "出图信息已修改，请重新生成预览后确认。";
                    RefreshActions();
                };
            _check.Click += async (_, __) => await Run("check");
            _prepare.Click += async (_, __) => await Run("preview");
            _run.Click += async (_, __) => await Run("export");
            _resume.Click += async (_, __) => await Run("preview");
            _cancel.Click += (_, __) =>
            {
                if (_visualReviewCancellation != null)
                {
                    _visualReviewCancellation.Cancel();
                    _status.Text = "已请求停止AI审图，已完成回执保留，其余待审。";
                    RefreshActions();
                    return;
                }
                _samplingJob?.RequestStop();
                _status.Text = "已请求停止，完成当前阶段后保留进度。";
                RefreshActions();
            };
            _open.Content = "成果目录"; _summary.Content = "整套图件"; _resume.Content = "继续生成"; _cancel.Content = "停止";
            _open.Click += async (_, __) => await OpenOutDir(); _summary.Click += async (_, __) => await OpenSummary();
            _doctor.Click += async (_, __) => await CheckEnvironment();
            _copyDiagnostic.Click += (_, __) => { if (_diagnostic != null) Clipboard.SetText(_diagnostic.ToJsonString()); };
            _saveParams.Click += (_, __) => SaveProjectParams(); _loadParams.Click += (_, __) => LoadProjectParams();
            var bodyScroll = new ScrollViewer { Content = root, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
            var primaryFooter = new StackPanel { Margin = new Thickness(0, 6, 0, 8) };
            primaryFooter.Children.Add(new Border
            {
                Height = 1,
                Background = UiTheme.Brush(UiTheme.Border),
                Margin = new Thickness(0, 0, 0, 8)
            });
            primaryFooter.Children.Add(_phase); primaryFooter.Children.Add(_status); primaryFooter.Children.Add(_prepare);
            var secondaryActions = new WrapPanel();
            secondaryActions.Children.Add(_summary); secondaryActions.Children.Add(_open);
            secondaryActions.Children.Add(_resume); secondaryActions.Children.Add(_cancel); primaryFooter.Children.Add(secondaryActions);
            var diagnostics = new StackPanel();
            var diagnosticActions = new WrapPanel(); diagnosticActions.Children.Add(_doctor); diagnosticActions.Children.Add(_copyDiagnostic);
            diagnostics.Children.Add(diagnosticActions); diagnostics.Children.Add(_log);
            primaryFooter.Children.Add(new Expander { Header = "运行日志", Content = diagnostics });
            var layout = new DockPanel(); DockPanel.SetDock(primaryFooter, Dock.Bottom); layout.Children.Add(primaryFooter);
            DockPanel.SetDock(bodyScroll, Dock.Top); layout.Children.Add(bodyScroll);
            UiTheme.FlattenNestedExpanders(layout);
            var host = new UserControl { Content = layout };
            host.Loaded += async (_, __) =>
            {
                if (!_initialLoadDone)
                {
                    _initialLoadDone = true; await LoadLayers();
                    // 首次打开时把地图定位到图斑图层：工程保存的视图常落在空白处，
                    // 用户第一眼看到白屏，会以为数据没加载。
                    await ZoomToParcelsOnceAsync();
                    if (!string.IsNullOrEmpty(_lastTaskRoot) && System.IO.Directory.Exists(_lastTaskRoot) &&
                        _layers.SelectedItem is LayerChoice restoredSource && SameTask(_lastSourcePath, restoredSource.Path) &&
                        string.Equals(_lastProjectUri, _sourceProjectUri, StringComparison.OrdinalIgnoreCase))
                    {
                        var restoreRoot = _lastTaskRoot;
                        _outDir.Text = restoreRoot;
                        var saved = await Task.Run(() => WorkbenchTaskReceipt.Read(restoreRoot));
                        if (!_busy && SameTask(restoreRoot, _outDir.Text))
                        {
                            _receipt = saved;
                            await RestorePreviewImages(restoreRoot, saved);
                            if (!_busy && SameTask(restoreRoot, _outDir.Text))
                                _status.Text = "已恢复上次任务引用；请核对资料后继续，不会自动执行。";
                            RefreshActions();
                        }
                    }
                }
                if (!_busy && !_tdtCredentialDirty && _imageryKind.SelectedIndex == 2)
                {
                    try { _updatingTdtCredential = true; var shared = ServiceCredentials.Load() ?? "";
                        if (_tiandituKey.Password != shared) _tiandituKey.Password = shared; }
                    catch (Exception ex) { _status.Text = "底图凭证读取失败：" + ex.Message; }
                    finally { _updatingTdtCredential = false; }
                }
            };
            SyncImagery();
            return host;
        }

        private WorkbenchJobService.JobLease BeginUiJob(string title, string root = "")
        {
            try { return WorkbenchJobService.Begin(title, root); }
            catch (InvalidOperationException ex) { _status.Text = ex.Message; RefreshActions(); return null; }
        }

        private void RefreshActions()
        {
            var busy = _busy || _refreshingMap;
            SetRunInputs(!busy);
            _prepare.IsEnabled = _check.IsEnabled = _doctor.IsEnabled = !busy;
            _run.IsEnabled = !busy && !_metadataDirty && _previewTaskRoot != null && _previewCanExport &&
                _designConfirmed.IsChecked == true && !string.IsNullOrWhiteSpace(_reviewer.Text);
            _resume.IsEnabled = !busy && _receipt != null && (bool?)_receipt["can_resume"] == true;
            var result = !busy && _receipt != null && (bool?)_receipt["can_open_result"] == true && SameTask((string)_receipt["root"], _outDir.Text);
            _open.IsEnabled = result;
            _summary.IsEnabled = result && !string.IsNullOrEmpty((string)_receipt?["summary_path"]);
            _cancel.IsEnabled = _visualReviewCancellation != null ? !_visualReviewCancellation.IsCancellationRequested :
                _samplingCanStop && _samplingJob != null && !_samplingJob.StopRequested && (_samplingJob.Status == "Running" || _samplingJob.Status == "Validating");
            _copyDiagnostic.IsEnabled = _diagnostic != null;
            _mapResults.Visibility = _mapPreviewChoices.Items.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            _delivery.Visibility = _mapResults.Visibility;
            _emptyPreview.Visibility = _mapResults.Visibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible;
            _prepare.Content = _mapPreviewChoices.Items.Count > 0 ? "重新生成预览" : "生成预览";
            if (_mapPreviewChoices.Items.Count > 0) UiTheme.Quiet(_prepare); else UiTheme.Primary(_prepare);
            _summary.Visibility = _summary.IsEnabled ? Visibility.Visible : Visibility.Collapsed;
            _open.Visibility = _open.IsEnabled ? Visibility.Visible : Visibility.Collapsed;
            _resume.Visibility = _resume.IsEnabled ? Visibility.Visible : Visibility.Collapsed;
            _cancel.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
            var canRedraw = !busy && _receipt != null && SameTask((string)_receipt["root"], _outDir.Text) &&
                ((bool?)_receipt["can_open_preview"] == true || (bool?)_receipt["can_open_result"] == true);
            _reviewMaps.IsEnabled = !busy && _mapPreviewChoices.Items.Count > 0 &&
                (SameTask(_previewTaskRoot, _outDir.Text) || canRedraw);
            _redrawAll.IsEnabled = canRedraw;
            _redrawSelected.IsEnabled = canRedraw && _mapPreviewChoices.SelectedItem is MapPreviewChoice;
            _mapScale.IsEnabled = _applyMapScale.IsEnabled = !busy && _mapPreviewChoices.SelectedItem is MapPreviewChoice;
            var task = WorkbenchJobService.Snapshot();
            var phase = (string)task["phase"] ?? "";
            var mapProgress = (string)task["detail"];
            var names = new Dictionary<string, string> { ["preflight"] = "资料检查", ["data_prepared"] = "评价单元", ["sampling_prepared"] = "采样设计",
                ["forms_prepared"] = "记录表", ["imagery_prepared"] = "影像准备", ["maps_built"] = "构建图件", ["maps_exported"] = "导出图件", ["completed"] = "处理完成" };
            var current = _receipt == null ? "" : (string)_receipt["status"];
            _phase.Text = busy ? ((string)task["status"] == "Cancelling" ? "正在停止，等待安全阶段结束" : "正在处理：" + ((string)task["title"] ?? "资料") + (names.TryGetValue(phase, out var name) ? " · " + name : "")) :
                current == "Prepared" ? "预览就绪 · 待人工确认" : current == "Succeeded" ? "技术处理完成 · 业务审核另行确认" :
                current == "Cancelled" ? "任务已停止 · 可核对进度后继续" : current == "Interrupted" ? "任务中断 · 核对资料后继续" : current == "Failed" ? "任务未通过 · 查看诊断后修正" : "";
            if (busy && !string.IsNullOrWhiteSpace(mapProgress)) _phase.Text += " · " + mapProgress;
            if (!busy && _mapRefreshPending && !_refreshingMap) QueueMapRefresh();
        }

        private void ShowError(Exception ex, string context = "gp")
        {
            _diagnostic = WorkbenchDiagnostics.Error(ex, context, _diagnosticSecrets.Concat(new[] { _tiandituKey.Password }).ToArray());
            _status.Text = (string)_diagnostic["message"] + "（" + (string)_diagnostic["code"] + "）";
            _log.AppendText((string)_diagnostic["detail"] + Environment.NewLine);
            try
            {
                var folder = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SamplingAssistant", "logs");
                System.IO.Directory.CreateDirectory(folder);
                System.IO.File.WriteAllText(System.IO.Path.Combine(folder, "workbench-" + (_samplingJob?.Id ?? Guid.NewGuid().ToString("N")) + ".json"), _diagnostic.ToJsonString());
            }
            catch (Exception logError) { _log.AppendText("诊断未落盘：" + WorkbenchDiagnostics.Redact(logError.Message) + Environment.NewLine); }
        }

        private async Task<JsonObject> CheckEnvironmentCore()
        {
            var tool = ToolboxLocator.GetToolPath("CheckWorkbenchEnvironment", "GridSampling.pyt");
            WorkbenchDiagnostics.CheckResources(System.IO.Path.GetDirectoryName(tool));
            var result = await Geoprocessing.ExecuteToolAsync(tool, Geoprocessing.MakeValueArray(_outDir.Text.Trim()),
                null, CancellationToken.None, null, GPExecuteToolFlags.GPThread | GPExecuteToolFlags.AddToHistory);
            if (result == null || result.IsFailed) throw new InvalidOperationException("环境检查失败：" + string.Join("；", result?.Messages?.Select(m => m.Text) ?? Enumerable.Empty<string>()));
            var info = JsonNode.Parse(result.ReturnValue).AsObject();
            info["clr_version"] = Environment.Version.ToString();
            info["addin_version"] = typeof(SamplingWizardPane).Assembly.GetName().Version.ToString();
            if (!(bool)info["arcpy"] || !(bool)info["output_writable"]) throw new InvalidOperationException("ArcPy或输出权限检查未通过。");
            var version = (string)info["pro_version"];
            if (Version.Parse(version) < new Version(3, 1)) throw new InvalidOperationException("插件要求ArcGIS Pro 3.1或更高版本。");
            return info;
        }

        private async Task CheckEnvironment()
        {
            if (_busy) return;
            using var job = BeginUiJob("环境检查");
            if (job == null) return;
            try
            {
                job.Running(); _diagnostic = await CheckEnvironmentCore();
                _status.Text = "环境检查通过：Pro " + (string)_diagnostic["pro_version"] + "，Python " + (string)_diagnostic["python_version"] + "；仅检查当前任务所需资源。";
                _log.AppendText(_diagnostic.ToJsonString() + Environment.NewLine);
                job.Finish("Succeeded");
            }
            catch (Exception ex) { ShowError(ex, "toolbox"); job.Finish("Failed"); }
            finally { job.Dispose(); RefreshActions(); }
        }

        private static FrameworkElement Header(string text) => UiKit.Section(text);

        private static TextBlock Hint(string text) => new TextBlock
        {
            Text = text,
            Style = UiTheme.CaptionStyle,
            Margin = new Thickness(0, 0, 0, 4)
        };

        private static TextBox Num(string value) => new TextBox
        {
            Text = value,
            Width = 70,
            Margin = new Thickness(0, 2, 10, 4)
        };

        private static UIElement Grid2(string labelA, TextBox boxA, string labelB, TextBox boxB)
        {
            var panel = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 2, 0, 0) };
            panel.Children.Add(new TextBlock { Text = labelA, Width = 130, VerticalAlignment = VerticalAlignment.Center, FontSize = 12 });
            panel.Children.Add(boxA);
            panel.Children.Add(new TextBlock { Text = labelB, Width = 130, VerticalAlignment = VerticalAlignment.Center, FontSize = 12 });
            panel.Children.Add(boxB);
            return panel;
        }

        private static string StationDefaultsPath => System.IO.Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SamplingAssistant", "production.json");

        private void LoadStationDefaults()
        {
            try
            {
                if (!System.IO.File.Exists(StationDefaultsPath)) return;
                using var saved = JsonDocument.Parse(System.IO.File.ReadAllText(StationDefaultsPath));
                if (saved.RootElement.TryGetProperty("org", out var value)) _stationOrg = value.GetString() ?? "";
                if (saved.RootElement.TryGetProperty("drawn_by", out value)) _stationDrawnBy = value.GetString() ?? "";
                if (saved.RootElement.TryGetProperty("reviewer", out value)) _stationReviewer = value.GetString() ?? "";
                if (saved.RootElement.TryGetProperty("last_task_root", out value)) _lastTaskRoot = value.GetString();
                if (saved.RootElement.TryGetProperty("last_source_path", out value)) _lastSourcePath = value.GetString();
                if (saved.RootElement.TryGetProperty("last_project_uri", out value)) _lastProjectUri = value.GetString();
                _org.Text = _stationOrg; _drawnBy.Text = _stationDrawnBy; _reviewer.Text = _stationReviewer;
            }
            catch (Exception ex) { _status.Text = "本机出图信息未读取：" + ex.Message; }
        }

        private void SaveStationDefaults()
        {
            try
            {
                var temporary = StationDefaultsPath + ".tmp." + Guid.NewGuid().ToString("N");
                System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(StationDefaultsPath));
                var saved = System.IO.File.Exists(StationDefaultsPath) ? JsonNode.Parse(System.IO.File.ReadAllText(StationDefaultsPath)).AsObject() : new JsonObject();
                saved["org"] = _org.Text.Trim(); saved["drawn_by"] = _drawnBy.Text.Trim(); saved["reviewer"] = _reviewer.Text.Trim();
                saved["last_task_root"] = _lastTaskRoot;
                saved["last_source_path"] = _lastSourcePath; saved["last_project_uri"] = _lastProjectUri;
                System.IO.File.WriteAllText(temporary, saved.ToJsonString());
                System.IO.File.Move(temporary, StationDefaultsPath, true);
                _stationOrg = _org.Text.Trim(); _stationDrawnBy = _drawnBy.Text.Trim(); _stationReviewer = _reviewer.Text.Trim();
            }
            catch (Exception ex) { _log.AppendText("本机出图信息未保存：" + ex.Message + Environment.NewLine); }
        }

        private void AutoConfigureSource()
        {
            if (!(_layers.SelectedItem is LayerChoice choice)) return;
            var key = _sourceProjectUri + "\0" + choice.Path;
            if (_configuredSource == key) return;
            _layers.ToolTip = choice.Path;
            _configuredSource = key;
            _status.Text = "";
            _projectNumber.Clear(); _projectName.Clear(); _county.Clear(); _town.Clear(); _village.Clear(); _sheetNo.Clear();
            _org.Text = _stationOrg; _drawnBy.Text = _stationDrawnBy; _reviewer.Text = _stationReviewer;
            _cell.Clear(); _minPoints.Text = "3"; _spacing.Text = "15"; _inset.Text = "5";
            _maxMu.Text = "50"; _gap.Text = "30"; _multipart.IsChecked = false;
            _imageryPath.Clear(); _serviceConfig.Clear();
            try { _imageryKind.SelectedIndex = string.IsNullOrWhiteSpace(ServiceCredentials.Load()) ? 0 : 2; }
            catch (Exception ex) { _status.Text = "底图凭证读取失败：" + ex.Message; }
            var home = ArcGIS.Desktop.Core.Project.Current?.HomeFolderPath;
            if (string.IsNullOrWhiteSpace(home)) home = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            _outDir.Text = System.IO.Path.Combine(home, "采样出图", DateTime.Now.ToString("yyyyMMdd_HHmmss") + "_" + Guid.NewGuid().ToString("N").Substring(0, 6));
            var folder = SourceFolderPath();
            if (folder != null && System.IO.File.Exists(System.IO.Path.Combine(folder, "项目参数.json")))
            {
                try { LoadProjectParams(); }
                catch (Exception ex) { _status.Text = "项目参数无效：" + ex.Message; }
            }
        }

        private void LoadMapPreviews(JsonElement report)
        {
            if (report.TryGetProperty("项目设计规则", out var rules)) LoadMapScales(rules);
            else _mapScales.Clear();
            _mapPreviewChoices.Items.Clear();
            if (!report.TryGetProperty("出图", out var maps) || !maps.TryGetProperty("outputs", out var outputs)) return;
            foreach (var output in outputs.EnumerateArray())
            {
                if (!output.TryGetProperty("jpg", out var file) || !System.IO.File.Exists(file.GetString())) continue;
                _mapPreviewChoices.Items.Add(new MapPreviewChoice
                {
                    TargetName = output.GetProperty("target").GetString(),
                    ScaleKey = output.TryGetProperty("scale_key", out var scaleKey) && scaleKey.ValueKind == JsonValueKind.String ? scaleKey.GetString() : null,
                    Scale = output.TryGetProperty("scale", out var mapScale) ? mapScale.GetDouble() : 0,
                    Title = output.TryGetProperty("target", out var title) ? title.GetString() : System.IO.Path.GetFileName(file.GetString()),
                    Path = file.GetString(), Kind = output.TryGetProperty("kind", out var kind) ? kind.GetString() : "",
                    RenderKey = output.TryGetProperty("render_key", out var key) && key.ValueKind == JsonValueKind.String ? key.GetString() : "",
                    ReviewContext = BuildMapReviewContext(output, report)
                });
            }
            var firstSample = _mapPreviewChoices.Items.OfType<MapPreviewChoice>().FirstOrDefault(c => c.Kind == "sample");
            if (firstSample != null) _mapPreviewChoices.SelectedItem = firstSample;
            else if (_mapPreviewChoices.Items.Count > 0) _mapPreviewChoices.SelectedIndex = 0;
            _reviewMaps.IsEnabled = !_busy && _mapPreviewChoices.Items.Count > 0;
        }

        private async Task RestorePreviewImages(string root, JsonObject receipt)
        {
            if ((bool?)receipt["can_open_preview"] != true && (bool?)receipt["can_open_result"] != true) return;
            try
            {
                var path = (string)receipt["report_path"];
                using var report = await Task.Run(() => JsonDocument.Parse(System.IO.File.ReadAllText(path)));
                if (!_busy && SameTask(root, _outDir.Text)) LoadMapPreviews(report.RootElement);
            }
            catch (Exception ex) { if (!_busy && SameTask(root, _outDir.Text)) ShowError(ex, "result"); }
        }

        private static string BuildMapReviewContext(JsonElement output, JsonElement report)
        {
            var expected = new Dictionary<string, object>();
            foreach (var name in new[] { "target", "title", "kind", "parcel", "unit", "scale", "planned_scale" })
                if (output.TryGetProperty(name, out var value)) expected[name] = value.Clone();
            if (report.TryGetProperty("项目信息", out var meta) && meta.ValueKind == JsonValueKind.Object)
                foreach (var name in new[] { "presentation", "map_date" })
                    if (meta.TryGetProperty(name, out var value)) expected[name] = value.Clone();
            if (output.TryGetProperty("checks", out var checks) && checks.ValueKind == JsonValueKind.Array)
                foreach (var check in checks.EnumerateArray())
                    if (check.TryGetProperty("layers", out var layers) && layers.ValueKind == JsonValueKind.Array)
                        foreach (var layer in layers.EnumerateArray())
                            if (layer.TryGetProperty("layer", out var name) && name.GetString() == "设计采样点" &&
                                layer.TryGetProperty("count", out var count)) expected["expected_design_point_count"] = count.Clone();
            return "按以下预期信息检查实际图面。核对点号、地块边界、比例尺、图例、网格、文字遮挡及影像空洞。" +
                "只按明确预期判断缺项，已关闭的索引小图、坐标注记或预览字样不判缺失。" +
                "不改GIS数据、布点数量、编号或比例；图面通过不代表业务验收通过。预期信息：" + JsonSerializer.Serialize(expected);
        }

        private static string ImageHash(string path)
        {
            using var file = System.IO.File.OpenRead(path);
            using var sha = System.Security.Cryptography.SHA256.Create();
            return Convert.ToHexString(sha.ComputeHash(file)).ToLowerInvariant();
        }

        private static void WriteMapReview(MapPreviewChoice choice, System.Text.Json.Nodes.JsonObject receipt, string before)
        {
            if (ImageHash(choice.Path) != before) throw new InvalidOperationException("写回前图件已经变化，请重新审图。");
            receipt["render_key"] = choice.RenderKey;
            var destination = choice.Path + ".visual-review.json";
            var temporary = destination + ".tmp." + Guid.NewGuid().ToString("N");
            try
            {
                System.IO.File.WriteAllText(temporary, receipt.ToJsonString(), new System.Text.UTF8Encoding(false));
                if (ImageHash(choice.Path) != before) throw new InvalidOperationException("审图图件已经变化，未写入回执。");
                System.IO.File.Move(temporary, destination, true);
                if (ImageHash(choice.Path) != before) throw new InvalidOperationException("写回后图件已经变化，当前回执已失效。");
            }
            finally { if (System.IO.File.Exists(temporary)) System.IO.File.Delete(temporary); }
        }

        private async Task<string> ReviewMapPreviewsAsync(string taskRoot, bool automatic)
        {
            if ((!automatic && _busy) || string.IsNullOrWhiteSpace(taskRoot) || _mapPreviewChoices.Items.Count == 0) return "";
            var choices = _mapPreviewChoices.Items.OfType<MapPreviewChoice>().ToList();
            var ownsBusy = !automatic;
            using var reviewJob = ownsBusy ? BeginUiJob("图件视觉复核", taskRoot) : null;
            if (ownsBusy && reviewJob == null) return "工作台已有任务，视觉复核未启动。";
            if (ownsBusy)
            {
                _busy = true;  reviewJob.Running();
            }
            using var cancellation = new System.Threading.CancellationTokenSource();
            _visualReviewCancellation = cancellation;
            RefreshActions();
            var completed = 0; var uncertain = 0; var failed = 0; string summary = "";
            try
            {
                if (System.IO.File.Exists(System.IO.Path.Combine(taskRoot, "cancel.request")))
                    throw new OperationCanceledException(cancellation.Token);
                var settings = ProAiBridge.SettingsStore.Load();
                if (string.IsNullOrWhiteSpace(settings.Model))
                {
                    throw new InvalidOperationException("尚未配置视觉模型，图件保留待审。");
                }
                settings.Endpoint();
                var client = new ProAiBridge.ModelClient();
                var allowedRoot = System.IO.Path.GetFullPath(taskRoot).TrimEnd('\\', '/') + System.IO.Path.DirectorySeparatorChar;
                foreach (var choice in choices)
                {
                    cancellation.Token.ThrowIfCancellationRequested();
                    if (!System.IO.Path.GetFullPath(choice.Path).StartsWith(allowedRoot, StringComparison.OrdinalIgnoreCase) ||
                        string.IsNullOrWhiteSpace(choice.RenderKey))
                        throw new InvalidOperationException("图件不属于当前任务或缺少渲染指纹，保留待审。");
                    _status.Text = "AI审图 " + (completed + 1) + "/" + choices.Count + "：" + choice.Title;
                    var before = ImageHash(choice.Path);
                    var receipt = await client.ReviewMapAsync(settings, choice.Path, choice.ReviewContext, cancellation.Token);
                    if ((string)receipt["image_sha256"] != before) throw new InvalidOperationException("图件在请求前后变化，回执未写入。");
                    cancellation.Token.ThrowIfCancellationRequested();
                    WriteMapReview(choice, receipt, before);
                    completed++;
                    if ((string)receipt["status"] == "FAIL") failed++;
                    else if ((string)receipt["status"] != "PASS") uncertain++;
                }
                summary = "AI图面检查完成 " + completed + "/" + choices.Count + " 幅，图面问题 " + failed + " 幅，待核 " + uncertain + " 幅；业务审核仍待确认。";
            }
            catch (OperationCanceledException)
            {
                summary = "AI审图已停止，完成 " + completed + "/" + choices.Count + " 幅；其余待审，业务审核仍待确认。";
            }
            catch (Exception ex)
            {
                summary = "AI审图未完成（" + completed + "/" + choices.Count + "），其余保留待审：" + ex.Message;
                _log.AppendText(summary + Environment.NewLine);
            }
            finally
            {
                _visualReviewCancellation = null;
                try
                {
                    var reportPath = CurrentReportPath(taskRoot);
                    var refresh = await Geoprocessing.ExecuteToolAsync(
                        ToolboxLocator.GetToolPath("RefreshMapVisualReviews", "GridSampling.pyt"), Geoprocessing.MakeValueArray(reportPath),
                        null, CancellationToken.None, null, GPExecuteToolFlags.GPThread);
                    if (refresh == null || refresh.IsFailed) throw new InvalidOperationException("审图回执已保留，报告刷新失败；须重新检查当前视觉状态。");
                    using var report = JsonDocument.Parse(System.IO.File.ReadAllText(reportPath));
                    if (!report.RootElement.TryGetProperty("出图", out var maps) || !maps.TryGetProperty("status", out var status))
                        throw new InvalidOperationException("报告中缺少更新后的逐图检查状态。");
                    _previewCanExport = status.GetString() != "FAIL" &&
                        (!report.RootElement.TryGetProperty("技术状态", out var technical) || technical.GetString() != "FAIL");
                }
                catch (Exception ex)
                {
                    _previewCanExport = false;
                    summary += " 报告状态待刷新：" + ex.Message;
                    _log.AppendText(ex.Message + Environment.NewLine);
                }
                _status.Text = summary;
                if (ownsBusy)
                {
                    _busy = false;
                    _receipt = await Task.Run(() => WorkbenchTaskReceipt.Read(taskRoot));
                    reviewJob.Finish("Succeeded"); reviewJob.Dispose();
                }
                RefreshActions();
            }
            return summary;
        }

        private void ShowMapPreview()
        {
            _mapPreview.Source = null;
            if (!(_mapPreviewChoices.SelectedItem is MapPreviewChoice choice)) return;
            _mapScale.Text = choice.ScaleKey != null && _mapScales.TryGetValue(choice.ScaleKey, out var scale)
                ? scale.ToString(System.Globalization.CultureInfo.InvariantCulture) : "自动";
            _mapScale.ToolTip = "当前 1:" + choice.Scale.ToString(System.Globalization.CultureInfo.InvariantCulture);
            try
            {
                var image = new System.Windows.Media.Imaging.BitmapImage();
                image.BeginInit(); image.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
                image.DecodePixelWidth = 1000; image.UriSource = new Uri(choice.Path); image.EndInit(); image.Freeze();
                _mapPreview.Source = image;
            }
            catch (Exception ex) { _status.Text = "图件预览无法读取：" + ex.Message; }
        }

        private void LoadMapScales(JsonElement rules)
        {
            _mapScales.Clear();
            if (rules.ValueKind == JsonValueKind.Object && rules.TryGetProperty("map_scales", out var scales) && scales.ValueKind == JsonValueKind.Object)
                foreach (var item in scales.EnumerateObject())
                    _mapScales[item.Name] = item.Value.GetDouble();
        }

        private async Task ApplyMapScale()
        {
            if (_busy || !(_mapPreviewChoices.SelectedItem is MapPreviewChoice choice)) return;
            if (string.IsNullOrEmpty(choice.ScaleKey))
            { _status.Text = "请先生成一次新预览，再设置逐幅比例尺。"; return; }
            var text = _mapScale.Text.Trim().Replace("1:", "").Replace("1：", "");
            if (text == "自动") text = "auto";
            else if (double.TryParse(text, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var scale)
                && !double.IsInfinity(scale) && !double.IsNaN(scale) && scale >= 1 && scale <= 10000000)
                text = scale.ToString(System.Globalization.CultureInfo.InvariantCulture);
            else { _status.Text = "比例尺请输入“自动”或1至10000000的分母，例如2000。"; return; }
            _designConfirmed.IsChecked = false;
            _previewCanExport = false;
            await RedrawMaps(false, text);
        }

        private async Task RedrawMaps(bool all, string scale = "")
        {
            if (_busy) return;
            var root = _outDir.Text.Trim();
            var choice = _mapPreviewChoices.SelectedItem as MapPreviewChoice;
            if (!all && choice == null) return;
            using var job = BeginUiJob(all ? "重绘整套图" : "重绘当前图", root);
            if (job == null) return;
            using var stopMonitor = new CancellationTokenSource();
            Task monitor = Task.CompletedTask;
            try
            {
                _busy = true; _samplingJob = job; _samplingCanStop = true;
                _designConfirmed.IsChecked = false; _previewCanExport = false;
                RefreshActions(); job.Running("maps_exported");
                monitor = job.MonitorSamplingAsync(root, "", stopMonitor.Token);
                var tool = ToolboxLocator.GetToolPath("RedrawAcceptanceMaps", "GridSampling.pyt");
                var result = await Geoprocessing.ExecuteToolAsync(tool, Geoprocessing.MakeValueArray(root, all ? "" : choice.TargetName, scale, SelectedServiceKey()),
                    null, CancellationToken.None, null, GPExecuteToolFlags.GPThread | GPExecuteToolFlags.AddToHistory);
                _receipt = await Task.Run(() => WorkbenchTaskReceipt.Read(root));
                if ((string)_receipt["status"] == "Cancelled") throw new OperationCanceledException();
                if (result == null || result.IsFailed)
                    throw new InvalidOperationException(string.Join("；", result?.Messages?.Select(m => m.Text) ?? Enumerable.Empty<string>()));
                if ((bool?)_receipt["can_open_preview"] != true)
                    throw new System.IO.InvalidDataException((string)_receipt["error"] ?? "重绘报告未通过完整性检查。");
                using var report = JsonDocument.Parse(System.IO.File.ReadAllText((string)_receipt["report_path"]));
                LoadMapPreviews(report.RootElement);
                await LoadPreparedLayers(report.RootElement, root);
                RememberTask(root);
                job.Finish("Prepared");
                _status.Text = all ? "整套图已重绘，地块和点位保持原设计；可查看整套PDF。" : "当前图已重绘，整套PDF已更新；其余图件沿用原图。";
            }
            catch (OperationCanceledException) { job.Finish("Cancelled"); _status.Text = "重绘已停止，原报告和已生成图件保留。"; }
            catch (Exception ex) { job.Finish("Failed"); ShowError(ex); }
            finally
            {
                stopMonitor.Cancel();
                try { await monitor; } catch (OperationCanceledException) { } catch (Exception ex) { TraceError(ex); }
                _busy = false; _samplingJob = null; _samplingCanStop = false;
                job.Dispose(); RefreshActions();
            }
        }

        private static void TraceError(Exception ex) => System.Diagnostics.Trace.TraceError(ex.ToString());

        private void OpenSelectedMap()
        {
            if (!(_mapPreviewChoices.SelectedItem is MapPreviewChoice choice)) return;
            try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(choice.Path) { UseShellExecute = true }); }
            catch (Exception ex) { _status.Text = "图件未打开：" + ex.Message; }
        }

        private void SyncImagery()
        {
            var kind = _imageryKind.SelectedIndex;
            _imageryPath.IsEnabled = kind == 1;
            _tiandituKey.IsEnabled = kind == 2 || kind == 3;
            _serviceConfig.IsEnabled = kind == 3;
            _localImageryOptions.Visibility = kind == 1 ? Visibility.Visible : Visibility.Collapsed;
            _serviceOptions.Visibility = kind == 3 ? Visibility.Visible : Visibility.Collapsed;
            _credentialOptions.Visibility = kind == 2 || kind == 3 ? Visibility.Visible : Visibility.Collapsed;
            _tiandituOptions.Visibility = kind == 2 ? Visibility.Visible : Visibility.Collapsed;
            _mapImagery.Visibility = _mapImagery.Items.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        }

        private void SyncScope(string preferred = null)
        {
            _scope.Items.Clear();
            _scope.Visibility = Visibility.Collapsed;
            var choice = _layers.SelectedItem as LayerChoice;
            if (choice == null) return;
            _scope.Items.Add(new ScopeChoice("all", "全部图斑（" + choice.FeatureCount + " 个）"));
            if (!string.IsNullOrWhiteSpace(choice.DefinitionWhere) && choice.QueryCount >= 0)
                _scope.Items.Add(new ScopeChoice("query", "当前定义查询范围（" + choice.QueryCount + " 个）"));
            if (choice.SelectedObjectIds.Count > 0)
                _scope.Items.Add(new ScopeChoice("selection", "只处理当前已选要素（" + choice.SelectedObjectIds.Count + " 个）"));
            _scope.SelectedIndex = _scope.Items.Count - 1;
            if (preferred != null)
                _scope.SelectedItem = _scope.Items.OfType<ScopeChoice>().FirstOrDefault(item => item.Mode == preferred) ?? _scope.SelectedItem;
            if (!string.IsNullOrWhiteSpace(choice.DefinitionWhere) && choice.QueryCount < 0) _scope.SelectedIndex = -1;
            if (choice.SelectedObjectIds.Count > 10000)
                _layerHint.Text = "当前选择超过 10,000 个要素；请先导出所选图斑为临时图层。";
            else
                _layerHint.Text = "共 " + choice.FeatureCount + " 块";
            _scope.Visibility = _scope.Items.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
        }

        private void BrowseFile(TextBox target)
        {
            if (_busy) return;
            var dialog = new Microsoft.Win32.OpenFileDialog
            {
                Filter = "影像文件|*.tif;*.tiff;*.img;*.jpg;*.png;*.dat|所有文件|*.*"
            };
            if (dialog.ShowDialog() == true) target.Text = dialog.FileName;
        }

        private void BrowseFolder(TextBox target)
        {
            if (_busy) return;
            // 用 Pro 自带目录选择框，避免 WinForms 程序集在本机出现两个版本导致编译歧义
            var dialog = new ArcGIS.Desktop.Catalog.OpenItemDialog
            {
                Title = "选择成果文件夹（建议新建一个空文件夹）",
                MultiSelect = false,
                Filter = ArcGIS.Desktop.Catalog.ItemFilters.Folders
            };
            if (dialog.ShowDialog() != true) return;
            var item = dialog.Items.FirstOrDefault();
            if (item != null) target.Text = item.Path;
        }

        /// <summary>
        /// 首次打开工作台时把地图定位到图斑/评价单元图层。
        /// 工程保存的视图常落在空白区域，用户第一眼看到白屏会以为数据没加载；
        /// 只在首次加载时调用一次，不覆盖之后的手动导航。
        /// </summary>
        private async Task ZoomToParcelsOnceAsync()
        {
            try
            {
                // 工程刚打开时地图视图可能还没激活，先激活再取 MapView，否则永远是白屏。
                var view = MapView.Active;
                IMapPane pane = null;
                if (view == null)
                {
                    pane = FrameworkApplication.Panes.OfType<IMapPane>().FirstOrDefault();
                    if (pane is Pane paneBase) paneBase.Activate();
                    for (var i = 0; i < 10 && view == null; i++)
                    {
                        await Task.Delay(300);
                        view = MapView.Active ?? pane?.MapView;
                    }
                }
                if (view == null) return;
                await QueuedTask.Run(() =>
                {
                    var map = view.Map;
                    if (map == null) return;
                    var layers = map.GetLayersAsFlattenedList().OfType<FeatureLayer>().ToList();
                    var target = layers.FirstOrDefault(l => l.Name.Contains("图斑"))
                                 ?? layers.FirstOrDefault(l => l.Name.Contains("单元"))
                                 ?? layers.FirstOrDefault();
                    if (target != null) view.ZoomTo(target);
                });
            }
            catch (Exception ex)
            {
                _log.AppendText("自动定位到图斑失败（不影响使用）：" + ex.Message + Environment.NewLine);
            }
        }

        private void QueueMapRefresh()
        {
            Dispatcher.InvokeAsync(async () =>
            {
                _mapRefreshPending = true;
                if (!_initialLoadDone || _busy || _refreshingMap) return;
                _refreshingMap = true;
                RefreshActions();
                try
                {
                    do { _mapRefreshPending = false; await LoadLayers(preserveBinding: true); }
                    while (_mapRefreshPending && !_busy);
                }
                finally { _refreshingMap = false; RefreshActions(); }
            });
        }

        private async Task StyleSourceMap()
        {
            if (_busy || !(_layers.SelectedItem is LayerChoice choice) || choice.FromFolder) return;
            var map = MapView.Active?.Map;
            if (map == null || map.URI != _sourceMapUri) return;
            try
            {
                await QueuedTask.Run(() =>
                {
                    if (_busy || MapView.Active?.Map?.URI != map.URI) return;
                    var selected = map.GetLayersAsFlattenedList().OfType<FeatureLayer>().FirstOrDefault(l => l.URI == choice.LayerUri);
                    if (selected == null) return;
                    var outline = SymbolFactory.Instance.ConstructStroke(ColorFactory.Instance.CreateRGBColor(220, 48, 48), 1.2);
                    var symbol = SymbolFactory.Instance.ConstructPolygonSymbol(ColorFactory.Instance.CreateRGBColor(0, 0, 0, 0), SimpleFillStyle.Null, outline);
                    selected.SetRenderer(new ArcGIS.Core.CIM.CIMSimpleRenderer { Symbol = symbol.MakeSymbolReference() });
                    int Rank(Layer layer)
                    {
                        if (layer is GroupLayer group) return group.Layers.Select(Rank).DefaultIfEmpty(500).Min();
                        if (layer.Name.Contains("问题")) return 0;
                        if (layer is FeatureLayer feature && (feature.ShapeType == ArcGIS.Core.CIM.esriGeometryType.esriGeometryPoint ||
                            feature.ShapeType == ArcGIS.Core.CIM.esriGeometryType.esriGeometryMultipoint)) return 10;
                        if (layer == selected || layer.Name.Contains("边界") || layer.Name.Contains("图斑")) return 20;
                        if (layer.Name.Contains("网格") || layer.Name.Contains("方格")) return 30;
                        if (layer is BasicRasterLayer || layer is TiledServiceLayer) return 900;
                        return 500;
                    }
                    var ordered = map.Layers.OrderBy(Rank).ToList();
                    for (var index = 0; index < ordered.Count; index++)
                        if (map.Layers[index] != ordered[index]) map.MoveLayer(ordered[index], index);
                    if (!map.Layers.SequenceEqual(ordered)) throw new InvalidOperationException("地图绘制顺序读回不一致。");
                });
            }
            catch (Exception ex) { _log.AppendText("地图显示更新失败：" + ex.Message + Environment.NewLine); }
        }

        private async Task LoadLayers(bool preserveBinding = false, bool allowBusy = false)
        {
            if (_busy && !allowBusy) return;
            try
            {
                var previousChoice = _layers.SelectedItem as LayerChoice;
                var previousScope = (_scope.SelectedItem as ScopeChoice)?.Mode ?? "all";
                var map = MapView.Active?.Map;
                if (map == null)
                {
                    if (preserveBinding && previousChoice?.FromFolder == true &&
                        _sourceProjectUri == (ArcGIS.Desktop.Core.Project.Current?.URI ?? "")) return;
                    _layerChoices.Clear(); _layers.Items.Clear();
                    _layerHint.Text = "请先打开一个地图视图，再回到这里。";
                    return;
                }
                var sourceProject = ArcGIS.Desktop.Core.Project.Current?.URI ?? "";
                var choices = await QueuedTask.Run(() =>
                {
                    var items = new List<LayerChoice>();
                    foreach (var layer in map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                        .Where(l => l.ShapeType == ArcGIS.Core.CIM.esriGeometryType.esriGeometryPolygon))
                    {
                        using (var table = layer.GetTable())
                        using (var selection = layer.GetSelection())
                        {
                            var definition = (layer.GetDefinition() as ArcGIS.Core.CIM.CIMFeatureLayer)?.FeatureTable?.DefinitionExpression ?? "";
                            long queryCount = table.GetCount();
                            if (!string.IsNullOrWhiteSpace(definition))
                            {
                                try { queryCount = table.GetCount(new ArcGIS.Core.Data.QueryFilter { WhereClause = definition }); }
                                catch { queryCount = -1; }
                            }
                            var choice = new LayerChoice(layer.Name, table.GetPath().ToString(), table.GetCount(),
                                selection.GetObjectIDs().ToList(), definition, queryCount, layer.URI);
                            choice.Fields = table.GetDefinition().GetFields().Select(f => f.Name).ToList();
                            items.Add(choice);
                        }
                    }
                    return items;
                });
                ProAiBridge.PaneState.EnsureProject(sourceProject, ArcGIS.Desktop.Core.Project.Current?.URI ?? "");
                ProAiBridge.PaneState.EnsureMap(map.URI, MapView.Active?.Map?.URI ?? "");
                if (preserveBinding && _sourceProjectUri == sourceProject)
                    choices.AddRange(_layers.Items.OfType<LayerChoice>().Where(choice => choice.FromFolder));
                var previous = choices.FirstOrDefault(c => c.Path == previousChoice?.Path && c.LayerUri == previousChoice?.LayerUri && c.FromFolder == previousChoice?.FromFolder);
                var keep = preserveBinding && previous != null && (previous.FromFolder || _sourceMapUri == map.URI) &&
                    _sourceProjectUri == sourceProject && previous.Fields.SequenceEqual(previousChoice.Fields) && previous.FeatureCount == previousChoice.FeatureCount;
                if (keep)
                {
                    try { ProAiBridge.PaneState.EnsureScopeUnchanged(previousChoice.SelectedObjectIds, previous.SelectedObjectIds,
                        previousChoice.DefinitionWhere, previous.DefinitionWhere, previousScope); }
                    catch (InvalidOperationException) { keep = false; }
                }
                _updatingLayers = keep;
                _layerChoices.Clear(); _layers.Items.Clear();
                _sourceProjectUri = sourceProject; _sourceMapUri = map.URI;
                foreach (var choice in choices)
                {
                    _layerChoices.Add(choice);
                    _layers.Items.Add(choice);
                }
                if (choices.Count == 0)
                {
                    _layerHint.Text = "当前地图没有面图层，请先把补充耕地图斑加进来。";
                    return;
                }
                var candidates = choices.Where(c => c.Fields.Any(f => new[] { "PE_ID", "DIKUAI_NO", "DKBH", "DKBM", "BSM", "PL_NAME" }.Contains(f.ToUpperInvariant()))
                    && !new[] { "网格", "方格", "评价单元", "索引", "容重" }.Any(word => c.Name.Contains(word))).ToList();
                if (previous != null) _layers.SelectedItem = previous;
                else if (candidates.Count == 1) _layers.SelectedItem = candidates[0];
                else if (choices.Count == 1) _layers.SelectedIndex = 0;
                else _layerHint.Text = "有多份图斑，请选择本次资料；其余设置自动处理。";
                SyncScope(keep ? previousScope : null);
                await LoadCurrentImagery(autoSelect: !keep);
                _updatingLayers = false;
                if (keep) await StyleSourceMap();
                SyncImagery();
                if (string.IsNullOrEmpty(_layerHint.Text))
                    _layerHint.Text = "找到 " + choices.Count + " 个面图层；程序会自动识别图斑编号字段。";
            }
            catch (Exception ex)
            {
                _layerHint.Text = "读取图层失败：" + ex.Message;
            }
            finally { _updatingLayers = false; }
        }

        private async Task LoadCurrentImagery(bool autoSelect = true)
        {
            if (_busy) return;
            try
            {
                var images = await QueuedTask.Run(() =>
                {
                    var result = new List<LayerChoice>();
                    var map = MapView.Active?.Map;
                    if (map != null)
                        foreach (var layer in map.GetLayersAsFlattenedList())
                        {
                            if (layer is ServiceLayer service && IsOnlineSource(service.URL))
                            {
                                var endpoint = new Uri(service.URL).GetLeftPart(UriPartial.Path).TrimEnd('/');
                                if (endpoint.EndsWith("/MapServer", StringComparison.OrdinalIgnoreCase))
                                    result.Add(new LayerChoice(layer.Name + "（在线服务）", endpoint, 0, null, "", 0, layer.URI));
                            }
                            else if (layer is RasterLayer raster)
                            {
                                var path = raster.GetPath();
                                if (path != null && path.IsFile)
                                {
                                    using var data = raster.GetRaster();
                                    var generated = new[] { "\\imagery\\", "\\basemap\\", "\\basemaps\\" }
                                        .Any(part => path.LocalPath.IndexOf(part, StringComparison.OrdinalIgnoreCase) >= 0);
                                    result.Add(new LayerChoice(layer.Name, path.LocalPath, 0, null, "", 0, layer.URI)
                                        { AutoImageryCandidate = data.GetBandCount() >= 3 && !generated });
                                }
                            }
                        }
                    return result;
                });
                var selectedPath = (_mapImagery.SelectedItem as LayerChoice)?.Path;
                _mapImagery.Items.Clear();
                foreach (var image in images) _mapImagery.Items.Add(image);
                var local = images.Where(i => i.AutoImageryCandidate).ToList();
                if (autoSelect && local.Count == 1) _mapImagery.SelectedItem = local[0];
                else _mapImagery.SelectedItem = images.FirstOrDefault(image => image.Path == selectedPath);
                if (autoSelect) _status.Text = local.Count == 1 ? "已使用地图影像。" : "";
            }
            catch (Exception ex) { _status.Text = "读取当前影像失败：" + ex.Message; }
        }

        private async Task PreviewOnline()
        {
            if (_busy) return;
            if (!(_layers.SelectedItem is LayerChoice choice)) { _status.Text = "请先选择图斑图层。"; return; }
            if (_imageryKind.SelectedIndex == 1) { _status.Text = "请在影像来源选择在线影像，再点击连接预览。"; return; }
            var provider = _imageryKind.SelectedIndex == 3 ? _serviceConfig.Text.Trim()
                : _imageryKind.SelectedIndex == 2 ? "tianditu" : "demo";
            var scope = (_scope.SelectedItem as ScopeChoice)?.Mode ?? "all";
            var key = SelectedServiceKey();
            _diagnosticSecrets = new[] { key, _tiandituKey.Password };
            using var job = BeginUiJob("在线影像预览");
            if (job == null) return;
            job.Running();
            _busy = true; RefreshActions();
            _onlinePreview.Source = null;
            _status.Text = "正在连接在线影像并核对地块；网络失败会显示原因，不能等同没有高清覆盖……";
            try
            {
                var source = await GetLayerPath(choice, scope);
                var folder = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "SamplingAssistant", "OnlinePreviews", Guid.NewGuid().ToString("N"));
                var tool = ToolboxLocator.GetToolPath("PreviewOnlineImagery", "GridSampling.pyt");
                var args = new[] { source, provider, key, folder,
                    scope == "selection" ? string.Join(",", choice.SelectedObjectIds) : "",
                    scope == "query" ? choice.DefinitionWhere : "" };
                var result = await Geoprocessing.ExecuteToolAsync(tool, Geoprocessing.MakeValueArray(args), null, CancellationToken.None, null, GPExecuteToolFlags.GPThread);
                var reportPath = System.IO.Path.Combine(folder, "在线影像预览报告.json");
                if (result == null || result.IsFailed || !System.IO.File.Exists(reportPath))
                {
                    throw new InvalidOperationException("在线预览未完成：" + string.Join("；", result?.Messages?.Select(m => m.Text) ?? Enumerable.Empty<string>()));
                }
                using var document = JsonDocument.Parse(System.IO.File.ReadAllText(reportPath));
                var report = document.RootElement;
                var meta = report.GetProperty("imagery");
                if (report.GetProperty("preview_png").ValueKind == JsonValueKind.String)
                {
                    var image = new System.Windows.Media.Imaging.BitmapImage();
                    image.BeginInit(); image.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
                    image.UriSource = new Uri(report.GetProperty("preview_png").GetString()); image.EndInit(); image.Freeze();
                    _onlinePreview.Source = image;
                    _status.Text = (report.GetProperty("usable_for_parcel_preview").GetBoolean() ? "已连接并取得真实在线影像；图斑 " : "已连接，地块影像覆盖不足，请换源或重试；图斑 ") + report.GetProperty("parcel_id").GetString()
                        + (meta.TryGetProperty("service_pixel_spacing_m", out var spacing) ? "；下载像元约 " + spacing.ToString() + " 米" : "")
                        + "。来源时相和位置精度仍待核。";
                }
                else _status.Text = meta.TryGetProperty("warning", out var warning) ? warning.GetString() : "暂未取到在线影像，请核对网络或授权。";
                job.Finish("Succeeded");
            }
            catch (Exception ex) { job.Finish("Failed"); ShowError(ex, "map"); }
            finally
            {
                _busy = false; job.Dispose(); RefreshActions();
            }
        }

        private async Task DiscoverFolder()
        {
            if (_busy) return;
            var dialog = new ArcGIS.Desktop.Catalog.OpenItemDialog
            {
                Title = "选择已有项目资料文件夹", MultiSelect = false,
                Filter = ArcGIS.Desktop.Catalog.ItemFilters.Folders
            };
            if (dialog.ShowDialog() != true) return;
            var folder = dialog.Items.FirstOrDefault()?.Path;
            if (string.IsNullOrWhiteSpace(folder)) return;
            using var job = BeginUiJob("扫描项目资料");
            if (job == null) return;
            job.Running();
            var catalog = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "acceptance-intake-" + Guid.NewGuid().ToString("N") + ".json");
            _busy = true;
            _status.Text = "正在扫描资料，不修改源文件……";
            try
            {
                var tool = ToolboxLocator.GetToolPath("DiscoverProjectData", "GridSampling.pyt");
                var result = await Geoprocessing.ExecuteToolAsync(tool, Geoprocessing.MakeValueArray(folder, catalog), null, CancellationToken.None, null, GPExecuteToolFlags.GPThread);
                if (result == null || !System.IO.File.Exists(catalog)) throw new InvalidOperationException("未生成资料清单，请检查数据能否读取。");
                using (var document = JsonDocument.Parse(System.IO.File.ReadAllText(catalog)))
                {
                    _layers.Items.Clear();
                    _mapImagery.Items.Clear();
                    foreach (var selector in _factorChoices.Values)
                    {
                        selector.Items.Clear();
                        selector.Items.Add(new FactorChoice(null, null));
                        selector.SelectedIndex = 0;
                    }
                    foreach (var candidate in document.RootElement.GetProperty("candidates").EnumerateArray())
                    {
                        var roles = candidate.GetProperty("roles").EnumerateArray().Select(r => r.GetString()).ToList();
                        var path = candidate.GetProperty("path").GetString();
                        var count = candidate.GetProperty("count");
                        var choice = new LayerChoice(candidate.GetProperty("name").GetString(), path,
                            count.ValueKind == JsonValueKind.Number ? count.GetInt64() : 0, null, "", 0, fromFolder: true);
                        if (candidate.TryGetProperty("fields", out var fieldsArray) && fieldsArray.ValueKind == JsonValueKind.Array)
                            choice.Fields = fieldsArray.EnumerateArray().Select(f => f.GetString()).ToList();
                        if (roles.Contains("parcels")) _layers.Items.Add(choice);
                        if (roles.Contains("imagery"))
                        {
                            choice.AutoImageryCandidate = candidate.TryGetProperty("band_count", out var bands)
                                && bands.ValueKind == JsonValueKind.Number && bands.GetInt32() >= 3
                                && (!candidate.TryGetProperty("demo_only", out var demo) || demo.ValueKind != JsonValueKind.True);
                            _mapImagery.Items.Add(choice);
                        }
                        var fields = candidate.GetProperty("field_mapping");
                        foreach (var role in _factorChoices.Keys)
                            if (roles.Contains(role) && fields.TryGetProperty(role, out var field))
                                _factorChoices[role].Items.Add(new FactorChoice(path, field.GetString()));
                    }
                    if (_layers.Items.Count == 1) _layers.SelectedIndex = 0;
                    var localImages = _mapImagery.Items.OfType<LayerChoice>().Where(c => c.AutoImageryCandidate).ToList();
                    if (localImages.Count == 1) _mapImagery.SelectedItem = localImages[0];
                    foreach (var selector in _factorChoices.Values)
                        if (selector.Items.Count == 2) selector.SelectedIndex = 1;
                    _status.Text = "扫描得到 " + _layers.Items.Count + " 份图斑、" + _mapImagery.Items.Count +
                        " 份影像；发现 " + document.RootElement.GetProperty("issues").GetArrayLength() + " 个读取问题。多个候选请明确选择。";
                }
            }
            catch (Exception ex) { job.Finish("Failed"); ShowError(ex); }
            finally
            {
                _busy = false;
                job.Finish("Succeeded"); job.Dispose(); RefreshActions();
                if (System.IO.File.Exists(catalog)) System.IO.File.Delete(catalog);
            }
        }

        private async Task Run(string taskAction)
        {
            if (_busy) return;
            if (taskAction == "export" && (_metadataDirty || !_previewCanExport || _designConfirmed.IsChecked != true || string.IsNullOrWhiteSpace(_reviewer.Text) ||
                _previewTaskRoot == null || !SameTask(_previewTaskRoot, _outDir.Text)))
            {
                _status.Text = "请先完成自动准备，在地图查看单元和点位，再填写设计确认人并勾选确认。";
                return;
            }
            var choice = _layers.SelectedItem as LayerChoice;
            if (choice == null)
            {
                _status.Text = "① 先在当前地图里选一个补充耕地图斑图层。";
                return;
            }
            var selectedScope = (_scope.SelectedItem as ScopeChoice)?.Mode ?? "all";
            if (_scope.SelectedItem == null && !string.IsNullOrWhiteSpace(choice.DefinitionWhere))
            {
                _status.Text = "图层定义查询无法验证，请修复查询并刷新资料后再生成。"; return;
            }
            if (selectedScope == "selection" && choice.SelectedObjectIds.Count > 10000)
            {
                _status.Text = "当前选择过大；请先导出所选图斑为临时图层再运行。";
                return;
            }
            if (string.IsNullOrWhiteSpace(_outDir.Text))
            {
                _status.Text = "④ 还没选成果文件夹。";
                return;
            }
            if (taskAction == "export" && string.IsNullOrWhiteSpace(_org.Text))
            {
                _delivery.IsExpanded = true; _org.Focus();
                _status.Text = "交付前填写编制单位，再生成一次预览；采样数据会复用。";
                return;
            }
            if (taskAction == "export" && string.IsNullOrWhiteSpace(_drawnBy.Text))
            {
                _delivery.IsExpanded = true; _drawnBy.Focus(); _status.Text = "正式导出前请填写制图人。"; return;
            }
            if (_imageryKind.SelectedIndex == 1 &&
                !System.IO.File.Exists(_imageryPath.Text.Trim()) && !System.IO.Directory.Exists(_imageryPath.Text.Trim()))
            {
                _status.Text = "选了项目自有影像，但路径不存在。";
                return;
            }
            if (_imageryKind.SelectedIndex == 2 && string.IsNullOrWhiteSpace(_tiandituKey.Password))
            {
                _status.Text = "选天地图影像要先填 key。";
                return;
            }
            if (_imageryKind.SelectedIndex == 3 && !System.IO.File.Exists(_serviceConfig.Text.Trim()) && !IsOnlineSource(_serviceConfig.Text.Trim()))
            {
                _status.Text = "请读取当前地图影像，或填写在线服务地址。";
                return;
            }

            var taskRoot = System.IO.Path.GetFullPath(_outDir.Text.Trim());
            using var job = BeginUiJob("采样与出图", taskRoot);
            if (job == null) return;
            using var monitorStop = new CancellationTokenSource();
            Task monitor = Task.CompletedTask;
            string previousRun = null;
            _samplingJob = job; _samplingCanStop = taskAction != "check";
            _receipt = null;
            _busy = true;
            if (taskAction == "preview") _designConfirmed.IsChecked = false;
            RefreshActions();
            _log.Clear();
            _status.Text = "正在处理；每个完成阶段都会保存进度……";
            try
            {
                previousRun = (string)(await Task.Run(() => WorkbenchTaskReceipt.Read(taskRoot)))["run_id"];
                _diagnostic = await CheckEnvironmentCore();
                if (job.StopRequested) throw new OperationCanceledException();
                var source = await GetLayerPath(choice, selectedScope);
                var provider = _imageryKind.SelectedIndex == 3 ? _serviceConfig.Text.Trim()
                    : _imageryKind.SelectedIndex == 2 ? "tianditu" : "demo";
                if (_imageryKind.SelectedIndex == 1 && !string.IsNullOrWhiteSpace(ServiceCredentials.Load())) provider = "tianditu";
                var imagery = _imageryKind.SelectedIndex == 1 ? _imageryPath.Text.Trim() : "";
                var selectionIds = selectedScope == "selection"
                    ? string.Join(",", choice.SelectedObjectIds)
                    : "";
                var sourceQuery = selectedScope == "all" ? "" : choice.DefinitionWhere;
                var confirmation = "";
                if (taskAction == "export")
                {
                    var missing = new List<string>();
                    if (string.IsNullOrWhiteSpace(_org.Text)) missing.Add("编制单位");
                    if (string.IsNullOrWhiteSpace(_drawnBy.Text)) missing.Add("制图人");
                    if (string.IsNullOrWhiteSpace(_reviewer.Text)) missing.Add("设计确认人");
                    if (_designConfirmed.IsChecked != true) missing.Add("设计核对勾选");
                    if (missing.Count > 0)
                    {
                        job.Finish("Failed", "缺少正式导出信息");
                        _status.Text = "正式导出前请补齐：" + string.Join("、", missing) + "。";
                        return;
                    }
                    confirmation = JsonSerializer.Serialize(new Dictionary<string, string> {
                        { "fingerprint", _previewFingerprint },
                        { "operator", _reviewer.Text.Trim() } });
                }
                var factorSources = new Dictionary<string, object>();
                foreach (var role in _factorChoices)
                    if (role.Value.SelectedItem is FactorChoice factor && factor.Path != null)
                        factorSources[role.Key] = new { path = factor.Path, field = factor.Field };
                var unitImages = new Dictionary<string, object>();
                foreach (var entry in _unitImageryChoices)
                    if (entry.Value.SelectedItem is LayerChoice image)
                        unitImages[entry.Key] = IsOnlineSource(image.Path) ? (object)new { provider = image.Path } : new { raster = image.Path };
                var toolPath = ToolboxLocator.GetToolPath("GridSamplingOneClick", "GridSampling.pyt");
                _diagnosticSecrets = new[] { SelectedServiceKey(), _tiandituKey.Password };
                var idField = _idField.SelectedIndex > 0 ? _idField.SelectedItem.ToString() : "";
                var args = new List<string>
                {
                    source, taskRoot, idField, "", "",
                    _maxMu.Text.Trim(), _gap.Text.Trim(), _multipart.IsChecked == true ? "true" : "false",
                    _cell.Text.Trim(), _minPoints.Text.Trim(), _spacing.Text.Trim(), _inset.Text.Trim(),
                    imagery, provider, SelectedServiceKey(), "300",
                    "补充耕地质量鉴定采样点位布置图", _sheetNo.Text.Trim(), _org.Text.Trim(),
                    _drawnBy.Text.Trim(), "", "false", selectionIds, taskAction, sourceQuery,
                    factorSources.Count == 0 ? "" : JsonSerializer.Serialize(factorSources), _projectNumber.Text.Trim(), confirmation,
                    unitImages.Count == 0 ? "" : JsonSerializer.Serialize(unitImages),
                    JsonSerializer.Serialize(new { project_name = _projectName.Text.Trim(), county = _county.Text.Trim(),
                        town = _town.Text.Trim(), village = _village.Text.Trim(),
                        project_rules = new { map_scales = _mapScales } })
                };
                RememberTask(taskRoot);
                job.Running();
                if (taskAction != "check") monitor = job.MonitorSamplingAsync(taskRoot, previousRun, monitorStop.Token);
                var result = await Geoprocessing.ExecuteToolAsync(toolPath, Geoprocessing.MakeValueArray(args.ToArray()),
                    null, CancellationToken.None, null, GPExecuteToolFlags.GPThread | GPExecuteToolFlags.AddToHistory | GPExecuteToolFlags.RefreshProjectItems);
                monitorStop.Cancel();
                try { await monitor; } catch (OperationCanceledException) { }
                var messages = result?.Messages == null ? new string[0]
                    : result.Messages.Where(m => !string.IsNullOrWhiteSpace(m.Text)).Select(m => m.Text).ToArray();
                foreach (var message in messages) _log.AppendText(WorkbenchDiagnostics.Redact(message, new[] { SelectedServiceKey() }) + Environment.NewLine);
                if (taskAction == "check")
                {
                    if (result == null || result.IsFailed) throw new InvalidOperationException(string.Join("；", messages));
                    job.Finish("Succeeded");
                    _status.Text = messages.LastOrDefault(m => m.StartsWith("资料检查结果：")) ?? "资料检查已结束，请查看检查明细。";
                    return;
                }
                _receipt = await Task.Run(() => WorkbenchTaskReceipt.Read(taskRoot, job.ObservedRunId));
                if ((string)_receipt["run_id"] == previousRun) throw new System.IO.InvalidDataException("本次任务未产生新的运行状态，旧报告不能作为本次成果。");
                if ((string)_receipt["status"] == "Cancelled") throw new OperationCanceledException();
                if (result == null || result.IsFailed) throw new InvalidOperationException(string.Join("；", messages));
                var canOpen = taskAction == "preview"
                    ? (bool?)_receipt["can_open_preview"] == true
                    : (bool?)_receipt["can_open_result"] == true;
                if (!canOpen) throw new System.IO.InvalidDataException((string)_receipt["error"] ?? "没有读到完整成果，不能判定成功。");
                job.Finish((string)_receipt["status"]);
                var reportPath = (string)_receipt["report_path"];
                {
                    using (var report = JsonDocument.Parse(System.IO.File.ReadAllText(reportPath)))
                    {
                        LoadMapPreviews(report.RootElement);
                        if (taskAction == "preview")
                        {
                            _metadataDirty = false;
                            await LoadPreparedLayers(report.RootElement, taskRoot);
                            var maps = report.RootElement.GetProperty("出图");
                            var previewCount = maps.GetProperty("outputs").GetArrayLength();
                            _status.Text = "已生成 " + _mapPreviewChoices.Items.Count + "/" + previewCount
                                + " 张预览。列表切图，点击图片放大；“整套图件”查看全部及缺项。";
                            if (!_previewCanExport) _status.Text += " 有失败项，修正后再交付。";
                            SaveStationDefaults();
                            var previewStatus = _status.Text;
                            var visualStatus = await ReviewMapPreviewsAsync(taskRoot, automatic: true);
                            _status.Text = previewStatus + " " + visualStatus;
                            return;
                        }
                        var map = report.RootElement.GetProperty("出图");
                        var mapStatus = map.GetProperty("status").GetString();
                        var count = map.GetProperty("outputs").GetArrayLength();
                        if (count == 0 || mapStatus == "FAIL")
                            _status.Text = "自动检查发现失败项；请打开成果目录查看逐图报告与缩略图。";
                        else if (mapStatus == "PASS")
                            _status.Text = "自动检查通过，共 " + count + " 幅；业务内容仍需人工确认。";
                        else
                            _status.Text = "成果已生成，共 " + count + " 幅；有待人工核项，请先查看缩略图和检查报告。";
                        var exportStatus = _status.Text;
                        var exportVisualStatus = await ReviewMapPreviewsAsync(taskRoot, automatic: true);
                        _status.Text = exportStatus + " " + exportVisualStatus;
                    }
                    SaveStationDefaults();
                }
            }
            catch (OperationCanceledException)
            {
                job.Finish("Cancelled");
                _status.Text = "任务已停止，已完成阶段保留；核对资料后可继续。";
            }
            catch (Exception ex)
            {
                job.Finish("Failed", WorkbenchDiagnostics.Redact(ex.Message));
                if (_receipt != null) { _receipt["can_open_result"] = false; _receipt["status"] = "Failed"; }
                ShowError(ex);
            }
            finally
            {
                monitorStop.Cancel();
                try { await monitor; } catch (OperationCanceledException) { } catch (Exception ex) { ShowError(ex); }
                if (taskAction != "check" && (_receipt == null || job.Status == "Cancelled"))
                {
                    _receipt = await Task.Run(() => WorkbenchTaskReceipt.Read(taskRoot));
                    _receipt["can_open_result"] = false; _receipt["status"] = job.Status;
                }
                _busy = false;
                _samplingJob = null;
                job.Dispose(); RefreshActions();
            }
        }

        private async Task LoadPreparedLayers(JsonElement report, string taskRoot)
        {
            if (!report.TryGetProperty("预览图层文件", out var value))
                throw new InvalidOperationException("未读到预览图层，请检查准备报告。");
            _previewTaskRoot = taskRoot;
            _previewFingerprint = report.GetProperty("输入指纹").GetString();
            _previewSignature = report.GetProperty("设计指纹").GetString();
            _previewRevision = report.GetProperty("设计修订号").GetInt32();
            var technicalFailed = report.TryGetProperty("技术状态", out var technical) && technical.GetString() == "FAIL";
            if (report.TryGetProperty("出图", out var maps) && maps.TryGetProperty("technical_status", out var mapTechnical))
                technicalFailed |= mapTechnical.GetString() == "FAIL";
            _previewCanExport = !technicalFailed;
            _pointIds.Items.Clear();
            BuildUnitImageryChoices(report);
            if (MapView.Active == null) return;
            var paths = value.EnumerateArray().Select(item => item.GetString()).ToList();
            try
            {
            var ids = await QueuedTask.Run(() =>
            {
                var map = MapView.Active?.Map;
                if (map == null) throw new InvalidOperationException("请先激活地图视图，再点击继续加载预览。");
                ProAiBridge.PaneState.EnsureProject(_sourceProjectUri, ArcGIS.Desktop.Core.Project.Current?.URI ?? "");
                ProAiBridge.PaneState.EnsureMap(_sourceMapUri, map.URI);
                var prior = map.GetLayersAsFlattenedList().OfType<GroupLayer>().FirstOrDefault(layer => layer.URI == _previewGroupUri);
                if (prior != null) map.RemoveLayer(prior);
                var group = LayerFactory.Instance.CreateGroupLayer(map, 0, "采样助手 · 本次设计预览");
                _previewGroupUri = group.URI;
                foreach (var path in paths.AsEnumerable().Reverse())
                    LayerFactory.Instance.CreateLayer(new Uri(path), group);
                var sample = group.GetLayersAsFlattenedList().OfType<FeatureLayer>().FirstOrDefault(layer => layer.Name == "设计采样点");
                var points = new List<string>();
                if (sample != null)
                {
                    using var cursor = sample.Search(new ArcGIS.Core.Data.QueryFilter { SubFields = "POINT_ID" });
                    while (cursor.MoveNext())
                    {
                        using var row = cursor.Current;
                        points.Add(Convert.ToString(row["POINT_ID"]));
                    }
                }
                return points.OrderBy(id => id).ToList();
            });
            foreach (var id in ids) _pointIds.Items.Add(id);
            }
            catch (Exception ex) { _log.AppendText("图件已生成，地图图层未加载：" + ex.Message + Environment.NewLine); }
        }

        internal async Task ReloadAfterAdjustment(string taskRoot)
        {
            _designConfirmed.IsChecked = false;
            if (_previewTaskRoot == null || !SameTask(taskRoot, _previewTaskRoot) || !SameTask(taskRoot, _outDir.Text))
            {
                _status.Text = "点位修改已保存到原任务，请回到对应目录重新准备预览：" + taskRoot;
                return;
            }
            await Run("preview");
        }

        private void BuildUnitImageryChoices(JsonElement report)
        {
            var previous = _unitImageryChoices.Where(entry => entry.Value.SelectedItem is LayerChoice)
                .ToDictionary(entry => entry.Key, entry => ((LayerChoice)entry.Value.SelectedItem).Path);
            _unitImageryChoices.Clear();
            _unitImageryPanel.Children.Clear();
            var images = _mapImagery.Items.OfType<LayerChoice>().ToList();
            if (System.IO.File.Exists(_imageryPath.Text.Trim()) && !images.Any(image => image.Path == _imageryPath.Text.Trim()))
                images.Add(new LayerChoice(System.IO.Path.GetFileName(_imageryPath.Text.Trim()), _imageryPath.Text.Trim(), 0, null, "", 0, fromFolder: true));
            foreach (var unit in report.GetProperty("单元明细").EnumerateArray().Take(500))
            {
                var id = unit.GetProperty("unit").GetString();
                _unitImageryPanel.Children.Add(Hint(id));
                var selector = new ComboBox { Margin = new Thickness(0, 2, 0, 4), IsEnabled = !_busy };
                selector.Items.Add("使用项目默认影像");
                foreach (var image in images) selector.Items.Add(image);
                selector.SelectedIndex = 0;
                if (previous.TryGetValue(id, out var prior))
                    foreach (var image in images)
                        if (string.Equals(image.Path, prior, StringComparison.OrdinalIgnoreCase)) selector.SelectedItem = image;
                selector.SelectionChanged += (_, __) =>
                {
                    ResetPreviewBinding();
                    if (!_busy) _status.Text = "逐单元影像已改变，请重新自动准备并确认预览。";
                };
                _unitImageryChoices[id] = selector;
                _unitImageryPanel.Children.Add(selector);
            }
            if (report.GetProperty("单元明细").GetArrayLength() > 500)
                _unitImageryPanel.Children.Add(Hint("本面板显示前500个单元；其余使用项目默认影像，可按范围分批准备。"));
        }

        private void ResetPreviewBinding(bool clearUnitSources = false)
        {
            _onlinePreview.Source = null;
            _previewTaskRoot = null;
            _previewFingerprint = null;
            _previewSignature = null;
            _previewCanExport = false;
            _metadataDirty = true;
            _receipt = null;
            _mapPreviewChoices.Items.Clear();
            _reviewMaps.IsEnabled = false;
            _mapPreview.Source = null;
            _pointIds.Items.Clear();
            _designConfirmed.IsChecked = false;
            PointAdjustmentTool.TaskRoot = null;
            PointAdjustmentTool.PointId = null;
            if (clearUnitSources)
            {
                _mapScales.Clear();
                _unitImageryChoices.Clear();
                _unitImageryPanel.Children.Clear();
            }
            RefreshActions();
        }

        private static bool SameTask(string first, string second)
        {
            try { return string.Equals(System.IO.Path.GetFullPath(first.Trim()).TrimEnd('\\', '/'),
                System.IO.Path.GetFullPath(second.Trim()).TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase); }
            catch { return false; }
        }

        private static bool IsOnlineSource(string path) => Uri.TryCreate(path, UriKind.Absolute, out var uri)
            && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps);

        private string SelectedServiceKey() => _imageryKind.SelectedIndex == 1 ? ServiceCredentials.Load() ?? ""
            : _imageryKind.SelectedIndex == 2 || _imageryKind.SelectedIndex == 3 ? _tiandituKey.Password.Trim() : "";

        private void SetRunInputs(bool enabled)
        {
            foreach (var control in new Control[] { _outDir, _layers, _scope, _idField, _imageryKind, _imageryPath, _mapImagery,
                _tiandituKey, _serviceConfig, _projectNumber, _org, _sheetNo, _drawnBy, _cell, _minPoints, _spacing, _inset,
                _maxMu, _gap, _multipart, _pointIds, _reviewer, _designConfirmed,
                _projectName, _county, _town, _village, _saveParams, _loadParams }) control.IsEnabled = enabled;
            foreach (var choice in _factorChoices.Values) choice.IsEnabled = enabled;
            foreach (var choice in _unitImageryChoices.Values) choice.IsEnabled = enabled;
            _reviewMaps.IsEnabled = enabled && _mapPreviewChoices.Items.Count > 0;
            if (enabled) SyncImagery();
        }

        private string CurrentReportPath(string taskRoot = null)
        {
            var root = taskRoot ?? _outDir.Text.Trim();
            var statePath = System.IO.Path.Combine(root, "task_state.json");
            if (System.IO.File.Exists(statePath))
            {
                using (var state = JsonDocument.Parse(System.IO.File.ReadAllText(statePath)))
                {
                    if (state.RootElement.TryGetProperty("report_path", out var path) && path.ValueKind == JsonValueKind.String)
                        return path.GetString();
                }
            }
            throw new System.IO.InvalidDataException("任务状态没有绑定本次成果报告，请重新生成预览。");
        }

        private void RememberTask(string root)
        {
            _lastTaskRoot = root; _lastSourcePath = (_layers.SelectedItem as LayerChoice)?.Path;
            _lastProjectUri = _sourceProjectUri; SaveStationDefaults();
        }

        private async Task<(string Path, string IdField, string LayerUri)> ParcelEditSource()
        {
            var choice = _layers.SelectedItem as LayerChoice ?? throw new InvalidOperationException("请先选择图斑来源。");
            var path = await GetLayerPath(choice, "all");
            var id = _idField.SelectedIndex > 0 ? _idField.SelectedItem.ToString() :
                new[] { "PE_ID", "DIKUAI_NO", "DKBH", "DKBM", "BSM", "PL_NAME" }.FirstOrDefault(name => choice.Fields.Contains(name));
            if (id == null) throw new InvalidOperationException("请在设置中指定图斑编号字段。");
            return (path, id, choice.LayerUri);
        }

        private async Task UseEditedParcels(string path, string idField, string projectUri, string mapUri)
        {
            ParcelEditingPanel.EnsureContext(projectUri, mapUri);
            var previous = (_layers.SelectedItem as LayerChoice)?.LayerUri;
            var kind = _imageryKind.SelectedIndex; var imagePath = _imageryPath.Text; var service = _serviceConfig.Text;
            var key = _tiandituKey.Password;
            await QueuedTask.Run(() =>
            {
                ParcelEditingPanel.EnsureContext(projectUri, mapUri);
                var map = MapView.Active?.Map ?? throw new InvalidOperationException("请先打开编辑来源所在地图。");
                var target = map.GetLayersAsFlattenedList().OfType<FeatureLayer>().FirstOrDefault(layer =>
                {
                    using var table = layer.GetTable();
                    return ProAiBridge.GpJob.SameFeatureSource(ProAiBridge.PaneState.DatasetPath(table.GetPath().ToString()), path);
                }) ?? LayerFactory.Instance.CreateLayer(new Uri(path), map, 0, "工作图斑") as FeatureLayer;
                if (target == null) throw new InvalidOperationException("新图斑图层未加载。");
                map.GetLayersAsFlattenedList().FirstOrDefault(layer => layer.URI == previous && layer.URI != target.URI)?.SetVisibility(false);
                target.SetVisibility(true); target.ClearSelection();
            });
            await LoadLayers(allowBusy: true);
            ParcelEditingPanel.EnsureContext(projectUri, mapUri);
            _layers.SelectedItem = _layers.Items.OfType<LayerChoice>().FirstOrDefault(choice =>
                ProAiBridge.GpJob.SameFeatureSource(choice.Path, path)) ?? throw new InvalidOperationException("新图斑未出现在来源列表。");
            _scope.SelectedItem = _scope.Items.OfType<ScopeChoice>().FirstOrDefault(scope => scope.Mode == "all");
            if (!_idField.Items.Contains(idField)) throw new InvalidOperationException("工作图斑缺少绑定的编号字段。");
            _idField.SelectedItem = idField;
            _imageryKind.SelectedIndex = kind; _imageryPath.Text = imagePath; _serviceConfig.Text = service;
            _tiandituKey.Password = key;
        }

        private async Task<string> GetLayerPath(LayerChoice choice, string scope)
        {
            if (choice.FromFolder) return choice.Path;
            ProAiBridge.PaneState.EnsureProject(_sourceProjectUri, ArcGIS.Desktop.Core.Project.Current?.URI ?? "");
            ProAiBridge.PaneState.EnsureMap(_sourceMapUri, MapView.Active?.Map?.URI ?? "");
            return await QueuedTask.Run(() =>
            {
                var map = MapView.Active?.Map;
                ProAiBridge.PaneState.EnsureProject(_sourceProjectUri, ArcGIS.Desktop.Core.Project.Current?.URI ?? "");
                ProAiBridge.PaneState.EnsureMap(_sourceMapUri, map?.URI ?? "");
                var matches = new List<string>();
                if (map != null)
                {
                    foreach (var layer in map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                        .Where(l => choice.LayerUri != null ? l.URI == choice.LayerUri : l.Name == choice.Name))
                    {
                        using (var table = layer.GetTable())
                        {
                            var path = ProAiBridge.PaneState.DatasetPath(table.GetPath().ToString());
                            if (string.Equals(path, choice.Path, StringComparison.OrdinalIgnoreCase))
                            {
                                using var currentSelection = layer.GetSelection();
                                var query = (layer.GetDefinition() as ArcGIS.Core.CIM.CIMFeatureLayer)?.FeatureTable?.DefinitionExpression ?? "";
                                ProAiBridge.PaneState.EnsureScopeUnchanged(choice.SelectedObjectIds, currentSelection.GetObjectIDs(), choice.DefinitionWhere, query, scope);
                                matches.Add(path);
                            }
                        }
                    }
                }
                if (matches.Count == 0)
                    throw new InvalidOperationException("所选图层已不在当前地图，或数据源路径已变化；请刷新后重新选择。");
                return matches[0];
            });
        }

        private static double Number(string text, double fallback)
            => double.TryParse(text, out var value) && !double.IsNaN(value) ? value : fallback;

        private void SyncIdFields()
        {
            _idField.Items.Clear();
            _idField.Items.Add("自动识别（推荐）");
            if (_layers.SelectedItem is LayerChoice choice && choice.Fields != null)
                foreach (var name in choice.Fields) _idField.Items.Add(name);
            _idField.SelectedIndex = 0;
        }

        private string SourceFolderPath()
        {
            if (!(_layers.SelectedItem is LayerChoice choice) || string.IsNullOrWhiteSpace(choice.Path))
                return null;
            try
            {
                var path = choice.Path;
                if (path.StartsWith("file:", StringComparison.OrdinalIgnoreCase))
                    path = new Uri(path).LocalPath;
                var gdb = path.IndexOf(".gdb", StringComparison.OrdinalIgnoreCase);
                if (gdb > 0) return System.IO.Path.GetDirectoryName(path.Substring(0, gdb + 4));
                return System.IO.Path.GetDirectoryName(path);
            }
            catch { return null; }
        }

        private void SaveProjectParams()
        {
            var folder = SourceFolderPath();
            if (folder == null)
            {
                _status.Text = "当前图层不是本地文件，无法写入项目参数；请直接把参数填在面板里。";
                return;
            }
            var path = System.IO.Path.Combine(folder, "项目参数.json");
            if (System.IO.File.Exists(path) && MessageBox.Show(
                    "项目参数已存在：\n" + path + "\n\n是否覆盖？", "保存项目参数",
                    MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes)
                return;
            var meta = new Dictionary<string, object>
            {
                { "project_name", _projectName.Text.Trim() },
                { "county", _county.Text.Trim() },
                { "town", _town.Text.Trim() },
                { "village", _village.Text.Trim() },
                { "org", _org.Text.Trim() },
                { "sheet_no", _sheetNo.Text.Trim() },
                { "drawn_by", _drawnBy.Text.Trim() }
            };
            var payload = new Dictionary<string, object>
            {
                { "project_number", _projectNumber.Text.Trim() },
                { "max_mu", Number(_maxMu.Text, 50.0) },
                { "min_points", (int)Number(_minPoints.Text, 3) },
                { "min_spacing_m", Number(_spacing.Text, 15.0) },
                { "inset_m", Number(_inset.Text, 5.0) },
                { "dpi", 300 },
                { "meta", meta }
            };
            if (System.IO.File.Exists(path))
            {
                try
                {
                    using (var previous = JsonDocument.Parse(System.IO.File.ReadAllText(path)))
                        foreach (var item in previous.RootElement.EnumerateObject())
                        {
                            if (item.Name == "meta" && item.Value.ValueKind == JsonValueKind.Object)
                            {
                                foreach (var entry in item.Value.EnumerateObject())
                                    if (!meta.ContainsKey(entry.Name)) meta[entry.Name] = entry.Value.Clone();
                            }
                            else if (!payload.ContainsKey(item.Name)) payload[item.Name] = item.Value.Clone();
                        }
                }
                catch (Exception ex) { _status.Text = "原项目参数无效，未覆盖：" + ex.Message; return; }
            }
            if (!string.IsNullOrWhiteSpace(_cell.Text))
                payload["cell_size_m"] = Number(_cell.Text, 0);
            else payload.Remove("cell_size_m");
            var savedRules = meta.TryGetValue("project_rules", out var existingRules)
                ? JsonNode.Parse(JsonSerializer.Serialize(existingRules)) as JsonObject : new JsonObject();
            savedRules ??= new JsonObject();
            savedRules["map_scales"] = JsonSerializer.SerializeToNode(_mapScales);
            meta["project_rules"] = savedRules;
            System.IO.File.WriteAllText(path,
                JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true }),
                new System.Text.UTF8Encoding(false));
            _status.Text = "项目参数已保存：" + path;
        }

        private void LoadProjectParams()
        {
            _mapScales.Clear();
            var folder = SourceFolderPath();
            var path = folder == null ? null : System.IO.Path.Combine(folder, "项目参数.json");
            if (path == null || !System.IO.File.Exists(path))
            {
                _status.Text = "未找到项目参数：请先选择本地图斑，或确认同目录存在 项目参数.json。";
                return;
            }
            using (var doc = JsonDocument.Parse(System.IO.File.ReadAllText(path)))
            {
                var root = doc.RootElement;
                if (root.TryGetProperty("project_number", out var value)) _projectNumber.Text = value.ToString();
                if (root.TryGetProperty("max_mu", out value)) _maxMu.Text = value.ToString();
                if (root.TryGetProperty("min_points", out value)) _minPoints.Text = value.ToString();
                if (root.TryGetProperty("min_spacing_m", out value)) _spacing.Text = value.ToString();
                if (root.TryGetProperty("inset_m", out value)) _inset.Text = value.ToString();
                if (root.TryGetProperty("gap_m", out value)) _gap.Text = value.ToString();
                if (root.TryGetProperty("allow_multipart", out value)) _multipart.IsChecked = value.ValueKind == JsonValueKind.True;
                if (root.TryGetProperty("cell_size_m", out value)) _cell.Text = value.ToString();
                if (root.TryGetProperty("meta", out var meta) && meta.ValueKind == JsonValueKind.Object)
                {
                    if (meta.TryGetProperty("project_rules", out var rules)) LoadMapScales(rules);
                    if (meta.TryGetProperty("project_name", out value)) _projectName.Text = value.ToString();
                    if (meta.TryGetProperty("county", out value)) _county.Text = value.ToString();
                    if (meta.TryGetProperty("town", out value)) _town.Text = value.ToString();
                    if (meta.TryGetProperty("village", out value)) _village.Text = value.ToString();
                    if (meta.TryGetProperty("org", out value) && !string.IsNullOrWhiteSpace(value.ToString())) _org.Text = value.ToString();
                    if (meta.TryGetProperty("sheet_no", out value)) _sheetNo.Text = value.ToString();
                    if (meta.TryGetProperty("drawn_by", out value) && !string.IsNullOrWhiteSpace(value.ToString())) _drawnBy.Text = value.ToString();
                }
            }
            _status.Text = "项目参数已加载：" + path;
            ResetPreviewBinding();
        }

        private async Task OpenSummary()
        {
            try
            {
                var root = _outDir.Text.Trim();
                _receipt = await Task.Run(() => WorkbenchTaskReceipt.Read(root));
                var summary = (string)_receipt["summary_path"];
                if ((bool?)_receipt["can_open_result"] != true || string.IsNullOrEmpty(summary))
                    throw new System.IO.InvalidDataException((string)_receipt["error"] ?? "本次摘要缺失，请重新生成或核对成果报告。");
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(summary) { UseShellExecute = true });
            }
            catch (Exception ex) { ShowError(ex, "result"); }
            finally { RefreshActions(); }
        }

        private async Task OpenOutDir()
        {
            try
            {
                var root = _outDir.Text.Trim();
                _receipt = await Task.Run(() => WorkbenchTaskReceipt.Read(root));
                if ((bool?)_receipt["can_open_result"] != true)
                    throw new System.IO.InvalidDataException((string)_receipt["error"] ?? "本次成果未通过完整性检查，请重新生成。");
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo((string)_receipt["result_dir"]) { UseShellExecute = true });
            }
            catch (Exception ex) { ShowError(ex, "result"); }
            finally { RefreshActions(); }
        }
    }
}
