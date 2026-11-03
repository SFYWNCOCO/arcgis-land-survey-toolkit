using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using AcceptanceAddin.Ui;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Framework.Contracts;
using Button = System.Windows.Controls.Button;
using CheckBox = System.Windows.Controls.CheckBox;
using ComboBox = System.Windows.Controls.ComboBox;

namespace ProAiBridge
{
    internal sealed class ChatPane : UserControl
    {
        internal int DraftLength => _prompt.Text.Length;
        private readonly TextBox _url = new TextBox();
        private readonly TextBox _model = new TextBox();
        private readonly PasswordBox _key = new PasswordBox();
        private readonly TextBox _timeout = new TextBox { Text = "120" };
        private readonly ComboBox _availableModels = new ComboBox { MaxDropDownHeight = 220 };
        private readonly Button _listModels = Button("读取可用模型");
        private readonly ComboBox _presets = new ComboBox { Margin = new Thickness(0, 0, 0, 5) };
        private readonly TextBox _prompt = new TextBox { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 72, MaxHeight = 120, MaxLength = 16000, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        private readonly StackPanel _transcript = new StackPanel();
        private readonly ScrollViewer _conversation = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        private readonly ListBox _commands = new ListBox { MaxHeight = 150, Visibility = Visibility.Collapsed };
        private readonly List<string> _inputHistory = new List<string>();
        private readonly Dictionary<string, ToolCardView> _toolCards = new Dictionary<string, ToolCardView>();
        private Border _emptyState;
        private Border _connectionPill;
        private TextBlock _connectionPillText;
        private Border _statusDot;
        private int _historyIndex = -1;
        private bool _restoringHistory;
        private bool _composing;
        private readonly Expander _basemapExpander = new Expander { Header = "底图设置", Margin = new Thickness(0, 0, 0, 4) };
        private readonly ComboBox _basemaps = new ComboBox();
        private readonly PasswordBox _amapKey = new PasswordBox(), _amapCode = new PasswordBox(), _tdtKey = new PasswordBox();
        private bool _amapKeyDirty, _amapCodeDirty, _tdtKeyDirty, _refreshingCredentials;
        private readonly CheckBox _follow = new CheckBox { Content = "跟随新消息", IsChecked = true, Margin = new Thickness(0, 3, 0, 3) };
        private readonly TextBlock _inputHint = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 3, 0, 0) };
        private readonly ProgressBar _progress = new ProgressBar { IsIndeterminate = true, Height = 3, Visibility = Visibility.Collapsed, Margin = new Thickness(0, 3, 0, 3) };
        private readonly Expander _settingsExpander = new Expander { Header = "模型设置", IsExpanded = true, Margin = new Thickness(0, 0, 0, 6) };
        private readonly Expander _tasksExpander = new Expander { Header = "任务与导出", Margin = new Thickness(0, 4, 0, 4), Visibility = Visibility.Collapsed };
        private readonly TextBlock _status = new TextBlock { Text = "就绪", TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 4) };
        private readonly Button _send = Button("发送");
        private readonly Button _cancel = Button("停止请求");
        private readonly Button _clear = Button("清空对话");
        private readonly Button _save = Button("保存模型设置");
        private readonly Button _test = Button("测试模型连接");
        private readonly ModelClient _client = new ModelClient();
        // 显式工具面档位：由 /chat、/sampling、/analysis 决定，关键词只作兜底
        private ModelClient.ToolScope _explicitScope = ModelClient.ToolScope.Chat;
        private readonly PaneState _pane = new PaneState();
        private readonly TextBlock _projectLabel = new TextBlock { Text = "正在读取当前工程…", TextWrapping = TextWrapping.Wrap, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 4) };
        private readonly TextBox _taskStatus = new TextBox { Text = "没有地理处理任务", IsReadOnly = true, TextWrapping = TextWrapping.Wrap, BorderThickness = new Thickness(0), Margin = new Thickness(0, 4, 0, 4), MaxHeight = 100, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        private readonly ComboBox _jobs = new ComboBox();
        private readonly Button _cancelJob = Button("取消所选任务");
        private readonly Button _openOutput = Button("打开导出文件");
        private readonly Button _openFolder = Button("打开所在文件夹");
        private readonly Button _loadJobOutput = Button("加载所选任务结果");
        private readonly DispatcherTimer _timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(5) };
        private CancellationTokenSource _refreshLifetime;
        private bool _refreshBusy;
        private bool _jobActionBusy;
        private bool _updatingJobs;
        private string _detailJobId;
        private string _map = "";
        private bool _changingMap;
        private string _project;
        private int _projectGeneration;
        private int _promptEditVersion;
        private CancellationTokenSource _running;

        internal ChatPane() { Content = CreateContent(); }
        private Control CreateContent()
        {
            // 统一视觉系统：先挂主题，再构建子控件，后续隐式样式才会生效。
            UiTheme.Apply(this);
            Bridge.Start();
            _project = Project.Current?.URI ?? "";

            // 外边距由工作台外壳统一给 12；这里为 0，避免 Hero 与内容左缘错位成 12/24。
            var root = new DockPanel { Margin = new Thickness(0), LastChildFill = true };

            // ① 状态条：工程/地图 + 桥接状态胶囊 + 常用检查动作（压缩为两行，给对话区留高度）
            var stateStack = new StackPanel();
            _projectLabel.TextWrapping = TextWrapping.Wrap;
            _projectLabel.Margin = new Thickness(0, 0, 0, 4);
            _projectLabel.FontSize = 11.5;
            _projectLabel.ToolTip = "此侧栏操作本窗口工程；DSH 多开 Pro 时可列出并选择目标工程。";
            stateStack.Children.Add(_projectLabel);
            var pillRow = new WrapPanel { Margin = new Thickness(0) };
            _connectionPill = Ui.Pill("Pro 桥接启动中", Tone.Warning);
            _connectionPill.Margin = new Thickness(0, 0, 8, 4);
            _connectionPillText = _connectionPill.Child as TextBlock;
            pillRow.Children.Add(_connectionPill);
            var check = Button("检查 Pro / DSH 连接");
            check.Click += async (_, __) => await DiagnoseAsync();
            var mapStatus = Button("查看地图状态");
            mapStatus.Click += async (_, __) => { await RefreshAsync(); Append(_projectLabel.Text); };
            UiTheme.Quiet(check); UiTheme.Quiet(mapStatus); UiTheme.Quiet(_clear);
            check.Margin = new Thickness(0, 0, 4, 4);
            pillRow.Children.Add(check); pillRow.Children.Add(mapStatus); pillRow.Children.Add(_clear);
            stateStack.Children.Add(pillRow);
            var statusCard = new Border { BorderBrush = UiTheme.Brush(UiTheme.Border), BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(0, 0, 0, 8), Child = stateStack };
            statusCard.Margin = new Thickness(0, 0, 0, 8);
            DockPanel.SetDock(statusCard, Dock.Top); root.Children.Add(statusCard);

            // ② 模型设置卡片（折叠）
            var settings = new StackPanel();
            settings.Children.Add(Ui.Caption("支持兼容 Chat Completions 和工具调用的模型。MCP 接入不需要在这里配置。"));
            var presets = _presets;
            var urls = new Dictionary<string, string> {
                ["自定义地址"] = "", ["OpenAI 兼容"] = "https://api.openai.com/v1",
                ["DeepSeek"] = "https://api.deepseek.com/v1", ["通义千问（兼容模式）"] = "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ["Ollama 本机"] = "http://127.0.0.1:11434/v1", ["LM Studio 本机"] = "http://127.0.0.1:1234/v1" };
            foreach (var name in urls.Keys) presets.Items.Add(name);
            presets.SelectedIndex = 0;
            presets.SelectionChanged += (_, __) => { if (presets.SelectedItem is string s && urls[s].Length > 0) _url.Text = urls[s]; };
            presets.Margin = new Thickness(0, 0, 0, 6);
            settings.Children.Add(presets);
            AddField(settings, "API 基础地址（包含 /v1 等服务路径）", _url);
            AddField(settings, "模型名（填写服务商实际支持的名称）", _model);
            AddField(settings, "API Key（本机加密保存；本地服务可留空）", _key);
            AddField(settings, "请求超时（秒，5–600）", _timeout);
            UiTheme.Quiet(_listModels);
            _listModels.Margin = new Thickness(0, 0, 0, 4);
            settings.Children.Add(_listModels);
            _availableModels.Margin = new Thickness(0, 0, 0, 6);
            settings.Children.Add(_availableModels);
            AutomationProperties.SetName(_timeout, "请求超时秒数");
            AutomationProperties.SetName(_availableModels, "服务返回的可用模型");
            _availableModels.SelectionChanged += (_, __) => { if (_running == null && _availableModels.SelectedItem is string name) _model.Text = name; };
            _listModels.Click += async (_, __) => await LoadModelsAsync();
            UiTheme.Quiet(_save); UiTheme.Quiet(_test);
            var buttons = new WrapPanel { Margin = new Thickness(0, 2, 0, 0) };
            buttons.Children.Add(_save); buttons.Children.Add(_test);
            settings.Children.Add(buttons);
            settings.Children.Add(Ui.Small("提问和工具返回的地图信息将发送到所选 API。更换服务或模型后会清空对话。"));
            _settingsExpander.Content = new ScrollViewer { Content = settings, MaxHeight = 200, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
            _settingsExpander.Margin = new Thickness(0, 0, 0, 8);
            DockPanel.SetDock(_settingsExpander, Dock.Top); root.Children.Add(_settingsExpander);

            // ③ 底图设置卡片（折叠）
            BuildBasemapPanel();
            _basemapExpander.Margin = new Thickness(0, 0, 0, 8);
            DockPanel.SetDock(_basemapExpander, Dock.Top); root.Children.Add(_basemapExpander);

            // ④ 底部输入与运行状态区
            var footer = new StackPanel();
            var taskPanel = new StackPanel();
            _jobs.Margin = new Thickness(0, 0, 0, 4);
            taskPanel.Children.Add(_jobs);
            _taskStatus.Background = UiTheme.Brush(UiTheme.SurfaceAlt);
            _taskStatus.BorderThickness = new Thickness(0);
            _taskStatus.Padding = new Thickness(8);
            _taskStatus.Margin = new Thickness(0, 0, 0, 6);
            taskPanel.Children.Add(_taskStatus);
            var job = Button("刷新任务");
            UiTheme.Quiet(job); UiTheme.Quiet(_cancelJob); UiTheme.Quiet(_openOutput); UiTheme.Quiet(_openFolder);
            var taskActions = new WrapPanel();
            taskActions.Children.Add(_loadJobOutput); taskActions.Children.Add(_cancelJob); taskActions.Children.Add(job);
            taskPanel.Children.Add(taskActions);
            var outputs = new WrapPanel(); outputs.Children.Add(_openOutput); outputs.Children.Add(_openFolder); taskPanel.Children.Add(outputs);
            _tasksExpander.Content = new ScrollViewer { Content = taskPanel, MaxHeight = 180, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
            _tasksExpander.Margin = new Thickness(0, 0, 0, 8);
            footer.Children.Add(_tasksExpander);
            _progress.Foreground = UiTheme.Brush(UiTheme.Accent);
            _progress.Background = UiTheme.Brush(UiTheme.Sunken);
            _progress.BorderThickness = new Thickness(0);
            footer.Children.Add(_progress);
            var statusRow = new Grid { Margin = new Thickness(0, 2, 0, 6) };
            statusRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            statusRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            _statusDot = Ui.Dot(UiTheme.TextMuted);
            statusRow.Children.Add(_statusDot);
            _status.Margin = new Thickness(0);
            _status.TextWrapping = TextWrapping.Wrap;
            _status.VerticalAlignment = VerticalAlignment.Center;
            _status.Style = UiTheme.CaptionStyle;
            Grid.SetColumn(_status, 1);
            statusRow.Children.Add(_status);
            UiTheme.WatchStatus(_status);
            System.ComponentModel.DependencyPropertyDescriptor
                .FromProperty(TextBlock.TextProperty, typeof(TextBlock))
                .AddValueChanged(_status, (_, __) => { if (_statusDot != null) _statusDot.Background = Ui.ToneBrush(UiTheme.ToneOf(_status.Text)); });
            footer.Children.Add(statusRow);
            _commands.Background = UiTheme.Brush(UiTheme.Surface);
            _commands.BorderBrush = UiTheme.Brush(UiTheme.Border);
            _commands.BorderThickness = new Thickness(1);
            _commands.Margin = new Thickness(0, 0, 0, 8);
            footer.Children.Add(_commands);
            var composer = new StackPanel();
            _prompt.Background = System.Windows.Media.Brushes.Transparent;
            _prompt.BorderThickness = new Thickness(0);
            _prompt.Padding = new Thickness(0);
            _prompt.Margin = new Thickness(0);
            composer.Children.Add(_prompt);
            var hintRow = new Grid { Margin = new Thickness(0, 6, 0, 0) };
            hintRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            hintRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            _inputHint.Style = UiTheme.SmallStyle;
            _inputHint.Margin = new Thickness(0, 0, 8, 0);
            _inputHint.VerticalAlignment = VerticalAlignment.Center;
            hintRow.Children.Add(_inputHint);
            var actions = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            _cancel.Margin = new Thickness(0, 0, 6, 0);
            _send.Margin = new Thickness(0);
            UiTheme.Primary(_send); UiTheme.Quiet(_cancel);
            actions.Children.Add(_cancel); actions.Children.Add(_send);
            Grid.SetColumn(actions, 1); hintRow.Children.Add(actions);
            composer.Children.Add(hintRow);
            footer.Children.Add(Ui.Surface(composer, new Thickness(10, 8, 10, 8), 6));
            // 快捷命令：单行横向滚动，避免两行 chips 挤掉对话区高度
            var chips = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 6, 0, 0) };
            foreach (var (command, tip) in new[]
            {
                ("/map", "当前地图与比例尺"), ("/layers", "列出图层"), ("/tasks", "地理处理任务"),
                ("/sampling", "采样与出图工具面"), ("/basemap", "底图设置")
            })
            {
                var chip = Ui.Chip(command, () => UseCommand(command));
                chip.ToolTip = tip;
                chips.Children.Add(chip);
            }
            footer.Children.Add(new ScrollViewer
            {
                Content = chips,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                VerticalScrollBarVisibility = ScrollBarVisibility.Disabled
            });
            var footerBar = Ui.FooterBar(footer);
            DockPanel.SetDock(footerBar, Dock.Bottom); root.Children.Add(footerBar);

            // ⑤ 对话区：跟随开关 + 圆角卡片内的消息流
            var conversationHead = new Grid { Margin = new Thickness(2, 0, 2, 6) };
            conversationHead.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            conversationHead.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            conversationHead.Children.Add(Ui.Section("对话"));
            _follow.Margin = new Thickness(0);
            _follow.VerticalAlignment = VerticalAlignment.Center;
            Grid.SetColumn(_follow, 1);
            conversationHead.Children.Add(_follow);
            _conversation.Content = _transcript;
            var conversationCard = Ui.Surface(_conversation, new Thickness(8, 6, 4, 6), 6);
            conversationCard.MinHeight = 180;
            conversationCard.ClipToBounds = true;
            conversationCard.Margin = new Thickness(0, 0, 0, 8);
            var conversationPanel = new DockPanel { LastChildFill = true };
            DockPanel.SetDock(conversationHead, Dock.Top);
            conversationPanel.Children.Add(conversationHead);
            conversationPanel.Children.Add(conversationCard);
            root.Children.Add(conversationPanel);

            _loadJobOutput.IsEnabled = _cancelJob.IsEnabled = _jobs.IsEnabled = false;
            _openOutput.IsEnabled = _openFolder.IsEnabled = false;
            AutomationProperties.SetName(_url, "API 基础地址"); AutomationProperties.SetName(_model, "模型名称");
            AutomationProperties.SetName(_key, "API Key"); AutomationProperties.SetName(_prompt, "输入地图操作要求");
            AutomationProperties.SetName(_transcript, "聊天与工具执行记录");
            AutomationProperties.SetName(_taskStatus, "所选任务详情"); AutomationProperties.SetName(_jobs, "地理处理任务列表");
            _prompt.TextChanged += (_, __) => { _promptEditVersion++; if (!_restoringHistory) _historyIndex = -1; UpdateComposer(); UpdateCommands(); };
            _commands.MouseDoubleClick += (_, __) => { if (_commands.SelectedItem is string command) { _prompt.Text = command; _prompt.CaretIndex = command.Length; _prompt.Focus(); } };
            _follow.ToolTip = "阅读历史记录时可取消勾选，新增消息不会跳到末尾。";
            _follow.Checked += (_, __) => _conversation.ScrollToEnd();
            _cancel.IsEnabled = false;
            _cancel.ToolTip = "停止当前模型请求；地理处理任务需在任务区单独取消。已完成操作不会回滚。";
            _send.Click += async (_, __) => await SendAsync(false);
            _test.Click += async (_, __) => await SendAsync(true);
            _cancel.Click += (_, __) => StopRequest();
            _clear.Click += (_, __) => ClearConversation();
            _save.Click += (_, __) => { try { SettingsStore.Save(ReadSettings()); _client.Clear(); _settingsExpander.IsExpanded = false; _status.Text = "设置已保存，密钥已用当前 Windows 用户加密。"; } catch (Exception ex) { _status.Text = ex.Message; } };
            job.Click += async (_, __) => await RefreshAsync();
            _cancelJob.Click += async (_, __) => await RunSelectedJobAsync(false);
            _loadJobOutput.Click += async (_, __) => await RunSelectedJobAsync(true);
            _jobs.SelectionChanged += (_, __) => { if (!_updatingJobs) ShowSelectedJob(); };
            _prompt.ToolTip = "Enter发送；Shift+Enter换行；/查看命令；空输入按↑查看历史；Esc停止请求。";
            TextCompositionManager.AddPreviewTextInputStartHandler(_prompt, (_, __) => _composing = true);
            TextCompositionManager.AddPreviewTextInputHandler(_prompt, (_, __) => _composing = false);
            _prompt.LostKeyboardFocus += (_, __) => _composing = false;
            _prompt.PreviewKeyDown += async (_, e) => {
                if (_composing || e.Key == Key.ImeProcessed) return;
                if ((e.Key == Key.Up || e.Key == Key.Down) && _commands.Visibility == Visibility.Visible)
                { e.Handled = true; _commands.SelectedIndex = (_commands.SelectedIndex + (e.Key == Key.Down ? 1 : -1) + _commands.Items.Count) % _commands.Items.Count; _commands.ScrollIntoView(_commands.SelectedItem); }
                else if (e.Key == Key.Tab && _commands.Visibility == Visibility.Visible && _commands.Items.Count > 0) { e.Handled = true; _prompt.Text = (string)(_commands.SelectedItem ?? _commands.Items[0]); _prompt.CaretIndex = _prompt.Text.Length; }
                else if (e.Key == Key.Enter && (Keyboard.Modifiers & ModifierKeys.Shift) == 0)
                { e.Handled = true; if (_commands.Visibility == Visibility.Visible && _commands.SelectedItem is string command) _prompt.Text = command; await SendAsync(false); }
                else if (e.Key == Key.Escape && _running != null) { e.Handled = true; StopRequest(); }
                else if (e.Key == Key.Up && (_prompt.Text.Length == 0 || _historyIndex >= 0) && _inputHistory.Count > 0)
                { e.Handled = true; RecallHistory(_historyIndex < 0 ? _inputHistory.Count - 1 : Math.Max(0, _historyIndex - 1)); }
                else if (e.Key == Key.Down && _historyIndex >= 0)
                { e.Handled = true; RecallHistory(_historyIndex + 1); }
            };
            _openOutput.Click += (_, __) => OpenOutput(false);
            _openFolder.Click += (_, __) => OpenOutput(true);
            _url.TextChanged += (_, __) => { _client.Clear(); _availableModels.ItemsSource = null; };
            _model.TextChanged += (_, __) => _client.Clear();
            _key.PasswordChanged += (_, __) => { _client.Clear(); _availableModels.ItemsSource = null; };
            _timeout.TextChanged += (_, __) => _client.Clear();
            try
            {
                var loaded = SettingsStore.Load(); _url.Text = loaded.BaseUrl; _model.Text = loaded.Model; _key.Password = loaded.ApiKey;
                _timeout.Text = loaded.RequestTimeoutSeconds.ToString();
                try { loaded.Endpoint(); _settingsExpander.IsExpanded = false; }
                catch (ArgumentException) { _settingsExpander.IsExpanded = true; }
            }
            catch (Exception ex) { _status.Text = "模型设置读取失败：" + ex.Message; }
            UpdateConnectionPill();
            UpdateComposer();
            Append("输入任务开始，或输入 / 查看命令。底图与模型可分别配置。");
            ShowEmptyState();
            var host = new UserControl { Content = root };
            _timer.Tick += async (_, __) => await RefreshAsync();
            host.Loaded += async (_, __) => { RefreshBasemapCredentials(); StartPolling(); await RefreshAsync(); };
            host.Unloaded += (_, __) => StopPolling();
            host.IsVisibleChanged += async (_, e) => { if ((bool)e.NewValue) { StartPolling(); await RefreshAsync(); } else StopPolling(); };
            return host;
        }

        private static Button Button(string text) => new Button { Content = text, Margin = new Thickness(0, 2, 6, 2) };
        private static void AddField(Panel panel, string label, Control input)
        {
            var caption = Ui.Caption(label);
            caption.Margin = new Thickness(0, 2, 0, 3);
            panel.Children.Add(caption);
            input.Margin = new Thickness(0, 0, 0, 6); panel.Children.Add(input);
        }
        private ModelSettings ReadSettings()
        {
            if (!int.TryParse(_timeout.Text, out var seconds)) throw new ArgumentException("请求超时须填写5至600之间的整数秒数。");
            return new ModelSettings { BaseUrl = _url.Text, Model = _model.Text, ApiKey = _key.Password, RequestTimeoutSeconds = seconds };
        }
        private void SetBusy(bool busy)
        {
            _test.IsEnabled = _save.IsEnabled = _clear.IsEnabled = _listModels.IsEnabled = !busy;
            _url.IsEnabled = _model.IsEnabled = _key.IsEnabled = _presets.IsEnabled = _timeout.IsEnabled = _availableModels.IsEnabled = !busy;
            _cancel.IsEnabled = busy;
            _progress.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
            UpdateComposer();
        }
        private void UpdateComposer()
        {
            _send.IsEnabled = _running == null && !string.IsNullOrWhiteSpace(_prompt.Text);
            _inputHint.Text = (_running == null ? "Enter发送 · Shift+Enter换行 · /命令" : "处理中，可编辑下一条 · Esc停止请求") +
                "　" + _prompt.Text.Length + "/16000";
        }
        private void StopRequest()
        {
            if (_running == null) return;
            _running.Cancel(); _cancel.IsEnabled = false;
            _status.Text = "已请求停止；已完成操作仍保留，GP任务需在任务区取消。";
        }
        private async Task LoadModelsAsync()
        {
            if (_running != null) return;
            _running = new CancellationTokenSource(); SetBusy(true); _status.Text = "正在读取可用模型…";
            _availableModels.ItemsSource = null;
            try
            {
                var models = await new ModelClient().ListModelsAsync(ReadSettings(), _running.Token);
                _running.Token.ThrowIfCancellationRequested();
                _availableModels.ItemsSource = models;
                _status.Text = "读取到" + models.Length + "个模型；选择后可测试连接，尚未自动保存。";
            }
            catch (OperationCanceledException) { _status.Text = "模型清单读取已取消。"; }
            catch (Exception ex) { _status.Text = "读取失败：" + ex.Message; }
            finally { _running.Dispose(); _running = null; SetBusy(false); }
        }
        /// <summary>把用户可见状态与桥接实际监听状态同步到状态胶囊（纯展示，不参与业务判断）。</summary>
        private void UpdateConnectionPill()
        {
            if (_connectionPill == null || _connectionPillText == null) return;
            var ready = Bridge.IsListening;
            var tone = ready ? Tone.Success : Tone.Warning;
            _connectionPill.Background = Ui.ToneSoftBrush(tone);
            _connectionPillText.Text = ready ? "Pro 桥接已连接" : "Pro 桥接启动中";
            _connectionPillText.Foreground = Ui.ToneBrush(tone);
        }
        private void UseCommand(string command)
        {
            _prompt.Text = command;
            _prompt.CaretIndex = command.Length;
            _prompt.Focus();
        }
        private void ShowEmptyState()
        {
            var examples = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Center };
            foreach (var example in new[]
            {
                "按 15 米间距布点并生成预览",
                "查看当前地图与图层",
                "检查图斑编号空值或重复",
                "生成整套采样图预览"
            })
            {
                examples.Children.Add(Ui.Chip(example, () => UseCommand(example)));
            }
            var empty = Ui.EmptyState("输入指令操作当前地图", "例如：把图斑按 15 米间距布点，并生成采样图预览。", examples);
            empty.Margin = new Thickness(0, 4, 0, 4);
            _transcript.Children.Add(empty);
            _emptyState = empty;
        }
        private void RemoveEmptyState()
        {
            if (_emptyState == null) return;
            if (_transcript.Children.Contains(_emptyState)) _transcript.Children.Remove(_emptyState);
            _emptyState = null;
        }
        private void Append(string text)
        {
            var offset = _conversation.VerticalOffset;
            RemoveEmptyState();
            _transcript.Children.Add(RenderMessage(text));
            TrimTranscript();
            if (_follow.IsChecked == true) _conversation.ScrollToEnd(); else _conversation.ScrollToVerticalOffset(offset);
        }
        private static FrameworkElement RenderMessage(string text)
        {
            var value = text ?? "";
            if (value.StartsWith("你：", StringComparison.Ordinal)) return Ui.Bubble(value.Substring(2), true);
            if (value.StartsWith("助手：", StringComparison.Ordinal)) return Ui.AssistantBubble(value.Substring(2));
            return Ui.SelectableNote(value);
        }
        /// <summary>只读读数框：等宽、可选中复制，用于工具参数/结果与地图回执。</summary>
        private static TextBox ReceiptText(string text) => new TextBox { Text = text, IsReadOnly = true, AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap, BorderThickness = new Thickness(0), Background = System.Windows.Media.Brushes.Transparent,
            Padding = new Thickness(8), Margin = new Thickness(0, 2, 0, 6), VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            FontFamily = new FontFamily("Cascadia Mono, Consolas"), FontSize = 10.5 };
        private void TrimTranscript() { while (_transcript.Children.Count > 100) _transcript.Children.RemoveAt(0); }
        private ToolCardView CreateToolCard(string name)
        {
            var header = Ui.ToolCard(name, out var phaseLabel, out var phaseDot);
            header.Margin = new Thickness(0, 0, 0, 2);
            var body = ReceiptText("");
            var bodyScroll = new ScrollViewer
            {
                Content = body,
                MaxHeight = 200,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
            };
            var details = new Expander
            {
                Header = "参数与结果",
                Content = bodyScroll,
                IsExpanded = false,
                FontSize = 11,
                FontWeight = FontWeights.Normal,
                Margin = new Thickness(0)
            };
            var card = new StackPanel { Margin = new Thickness(16, 3, 0, 5) };
            card.Children.Add(header);
            card.Children.Add(details);
            AutomationProperties.SetName(card, "工具调用：" + name);
            return new ToolCardView { Root = card, Body = body, PhaseLabel = phaseLabel, PhaseDot = phaseDot };
        }
        private void ToolTrace(string name, string phase, string detail)
        {
            var running = phase == "running";
            if (running || !_toolCards.TryGetValue(name, out var card))
            {
                RemoveEmptyState();
                card = CreateToolCard(name);
                card.Body.Text = (running ? "参数：" : "结果：") + detail;
                _toolCards[name] = card; _transcript.Children.Add(card.Root); TrimTranscript();
            }
            else card.Body.Text += Environment.NewLine + "结果：" + detail;
            var phaseText = phase switch
            {
                "running" => "执行中",
                "completed" => "已返回",
                "skipped" => "已跳过",
                "cancelled" => "已停止",
                _ => "失败"
            };
            var tone = phase switch
            {
                "running" => Tone.Warning,
                "completed" => Tone.Success,
                "skipped" => Tone.Info,
                "cancelled" => Tone.Neutral,
                _ => Tone.Danger
            };
            Ui.SetPhase(tone, card.PhaseLabel, card.PhaseDot, phaseText);
            if (!running) _toolCards.Remove(name);
            if (_follow.IsChecked == true) _conversation.ScrollToEnd();
        }
        private void ClearConversation() { _client.Clear(); _transcript.Children.Clear(); _toolCards.Clear(); _emptyState = null; ShowEmptyState(); _status.Text = "已清空对话"; }
        private void RecallHistory(int index)
        {
            _historyIndex = index >= _inputHistory.Count ? -1 : index; _restoringHistory = true;
            try { _prompt.Text = _historyIndex < 0 ? "" : _inputHistory[_historyIndex]; _prompt.CaretIndex = _prompt.Text.Length; }
            finally { _restoringHistory = false; }
        }
        private void UpdateCommands()
        {
            var prefix = _prompt.Text.TrimStart();
            var choices = prefix.StartsWith("/") && !prefix.Contains("\n") ? PaneState.Commands.Select(c => "/" + c).Where(c => c.StartsWith(prefix)).ToArray() : Array.Empty<string>();
            _commands.ItemsSource = choices; _commands.Visibility = choices.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            if (choices.Length > 0) _commands.SelectedIndex = 0;
        }
        private async Task<bool> RunCommandAsync(string text)
        {
            var command = PaneState.ParseCommand(text);
            if (command == null) return false;
            switch (command)
            {
                case "help": Append("/map 当前地图　/layers 图层\n/tasks 任务　/basemap 底图设置\n/sampling 采样与出图（工具面切到采样）\n/analysis 分析工具面　/chat 基础工具面\n/model 模型设置　/clear 清空对话　/stop 停止请求\nEnter发送；Shift+Enter换行；空输入按↑查看历史。"); break;
                case "map": await DirectAsync("get_status", new JsonObject()); break;
                case "layers": await DirectAsync("list_layers", new JsonObject()); break;
                case "tasks": _tasksExpander.IsExpanded = true; await RefreshAsync(); break;
                case "sampling": _explicitScope = ModelClient.ToolScope.Sampling; _client.ResetScope(); await DirectAsync("open_acceptance_workspace", new JsonObject { ["page"] = "sampling" }); break;
                case "analysis": _explicitScope = ModelClient.ToolScope.Analysis; _client.ResetScope(); break;
                case "chat": _explicitScope = ModelClient.ToolScope.Chat; _client.ResetScope(); break;
                case "basemap": _basemapExpander.IsExpanded = true; break;
                case "model": _settingsExpander.IsExpanded = true; break;
                case "clear": ClearConversation(); break;
                case "stop": StopRequest(); break;
            }
            _prompt.Clear(); return true;
        }
        private void BuildBasemapPanel()
        {
            _amapKey.PasswordChanged += (_, __) => { if (!_refreshingCredentials) _amapKeyDirty = true; };
            _amapCode.PasswordChanged += (_, __) => { if (!_refreshingCredentials) _amapCodeDirty = true; };
            _tdtKey.PasswordChanged += (_, __) => { if (!_refreshingCredentials) _tdtKeyDirty = true; };
            var panel = new StackPanel();
            foreach (var source in BasemapPreview.PreviewSources) _basemaps.Items.Add(BasemapPreview.Label(source));
            _basemaps.SelectedIndex = 0; _basemaps.Margin = new Thickness(0, 0, 0, 6); panel.Children.Add(_basemaps);
            AddField(panel, "高德 JS API Key", _amapKey); AddField(panel, "高德安全密钥", _amapCode); AddField(panel, "天地图 Key", _tdtKey);
            var save = Button("保存底图设置"); var open = Button("预览当前地图位置");
            UiTheme.Quiet(save); UiTheme.Quiet(open);
            var actions = new WrapPanel(); actions.Children.Add(save); actions.Children.Add(open); panel.Children.Add(actions);
            panel.Children.Add(Ui.Small("浏览参考，原始分辨率与定位精度待核。阿里云/单位授权服务在采样助手选择 XYZ/WMTS 配置。"));
            panel.Children.Add(Ui.Small("区图（行政区划）请让助手用 add_basemap source=amap_district 加载矢量界线与驻地注记，不支持浏览器预览。"));
            AutomationProperties.SetName(_basemaps, "底图来源"); AutomationProperties.SetName(_amapKey, "高德 JS API Key");
            AutomationProperties.SetName(_amapCode, "高德安全密钥"); AutomationProperties.SetName(_tdtKey, "天地图 Key");
            save.Click += (_, __) => {
                try
                {
                    BasemapPreview.SaveChanges(_amapKeyDirty ? _amapKey.Password : null, _amapCodeDirty ? _amapCode.Password : null, _tdtKeyDirty ? _tdtKey.Password : null);
                    _amapKeyDirty = _amapCodeDirty = _tdtKeyDirty = false; RefreshBasemapCredentials();
                    _status.Text = "底图设置已加密保存；尚未验证服务授权。";
                }
                catch (Exception ex) { _status.Text = ex.Message; }
            };
            open.Click += async (_, __) => await DirectAsync("open_basemap_preview", new JsonObject { ["source"] = BasemapPreview.PreviewSources[_basemaps.SelectedIndex], ["confirm"] = true });
            RefreshBasemapCredentials();
            _basemapExpander.Content = new ScrollViewer { Content = panel, MaxHeight = 240, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        }
        private void RefreshBasemapCredentials()
        {
            try
            {
                var value = BasemapPreview.Load(); _refreshingCredentials = true;
                if (!_amapKeyDirty) _amapKey.Password = value.AmapKey;
                if (!_amapCodeDirty) _amapCode.Password = value.AmapSecurityCode;
                if (!_tdtKeyDirty) _tdtKey.Password = value.TiandituKey;
            }
            catch { _status.Text = "底图设置读取失败，请重新填写并保存。"; }
            finally { _refreshingCredentials = false; }
        }
        private async Task DirectAsync(string name, JsonObject args, string expectedProject = null)
        {
            var project = expectedProject ?? Project.Current?.URI ?? "";
            var map = MapView.Active?.Map?.URI ?? "";
            try
            {
                if (project.Length == 0) throw new InvalidOperationException("请先打开任务所属工程。");
                var result = await MapActions.ExecuteAsync(name, args, CancellationToken.None, project, map);
                PaneState.EnsureProject(project, Project.Current?.URI ?? "");
                if (ToolProtocol.RequiresMap(name)) PaneState.EnsureMap(map, MapView.Active?.Map?.URI ?? "");
                Append(name + "：" + result.ToJsonString()); _status.Text = "操作完成";
            }
            catch (Exception ex) { Append("操作失败：" + ex.Message); _status.Text = "操作失败：" + ex.Message; }
        }
        private async Task RunSelectedJobAsync(bool load)
        {
            if (_jobActionBusy) return;
            if (_jobs.SelectedItem is not JobChoice selected) { _status.Text = "请先选择任务。"; return; }
            var latest = _pane.Jobs.FirstOrDefault(node => (string)node["job_id"] == selected.Id);
            if (!load && !PaneState.CanCancelJob(latest, Project.Current?.URI ?? ""))
            { _status.Text = "当前任务不需要取消，或已不属于当前工程；请刷新任务。"; return; }
            if (load && !selected.CanLoad) { _status.Text = "请先选择已成功生成要素结果的任务。"; return; }
            _jobActionBusy = true; ShowSelectedJob();
            try
            {
                var input = new JsonObject { ["job_id"] = selected.Id };
                if (load) input["confirm"] = true;
                await DirectAsync(load ? "load_job_output" : "cancel_job", input, selected.Project);
                await RefreshAsync();
            }
            finally { _jobActionBusy = false; ShowSelectedJob(); }
        }
        private async Task SendAsync(bool test)
        {
            if (!test)
            {
                try
                {
                    if (_running != null) { if (_prompt.Text.Trim() == "/stop") await RunCommandAsync(_prompt.Text); return; }
                    if (await RunCommandAsync(_prompt.Text)) return;
                }
                catch (Exception ex) { _status.Text = ex.Message; return; }
            }
            if (_running != null) return;
            var prompt = test ? "这是连接测试，请只回复：连接成功。不要调用任何工具。" : _prompt.Text;
            if (string.IsNullOrWhiteSpace(prompt)) { _status.Text = "请先输入问题。"; return; }
            ModelSettings settings;
            try { settings = ReadSettings(); settings.Endpoint(); SettingsStore.Save(settings); }
            catch (Exception ex) { _settingsExpander.IsExpanded = true; _status.Text = "请检查模型设置：" + ex.Message; return; }
            var expectedProject = Project.Current?.URI ?? "";
            var expectedMap = MapView.Active?.Map?.URI ?? "";
            if (!test && expectedProject.Length == 0) { _status.Text = "请先打开工程，再发送地图操作请求。"; return; }
            if (_project != expectedProject)
            {
                _project = expectedProject; _projectGeneration++; _client.Clear(); _pane.Reset();
                _openOutput.IsEnabled = _openFolder.IsEnabled = false;
                _jobs.ItemsSource = null; _taskStatus.Text = "没有地理处理任务";
            }
            if (_map != expectedMap) { _map = expectedMap; _projectGeneration++; _client.Clear(); }
            var generation = _projectGeneration;
            var sentPromptVersion = _promptEditVersion;
            if (!test) { if (_inputHistory.LastOrDefault() != prompt) _inputHistory.Add(prompt); if (_inputHistory.Count > 50) _inputHistory.RemoveAt(0); _historyIndex = -1; }
            _running = new CancellationTokenSource();
            _timer.Interval = TimeSpan.FromSeconds(1.5);
            SetBusy(true); Append("你：" + prompt);
            var ledgerStatus = "ok";
            var ledgerError = "";
            ModelClient runClient = null;
            try
            {
                var client = test ? new ModelClient() : _client;
                runClient = client;
                var answer = await client.RunAsync(settings, prompt, async (name, args, token) => {
                    JsonNode result;
                    _changingMap = name == "activate_map";
                    try
                    {
                        result = await MapActions.ExecuteAsync(name, args, token, expectedProject, expectedMap);
                        if (name == "activate_map")
                        {
                            expectedMap = (string)result["map_uri"]; _map = expectedMap; _client.Clear();
                        }
                    }
                    finally { _changingMap = false; }
                    PaneState.EnsureProject(expectedProject, Project.Current?.URI ?? "");
                    PaneState.EnsureMap(expectedMap, MapView.Active?.Map?.URI ?? "");
                    if (generation != _projectGeneration) throw new OperationCanceledException("工程已切换。");
                    if (_pane.RecordOutput(name, result)) { _openOutput.IsEnabled = _openFolder.IsEnabled = true; _tasksExpander.Visibility = Visibility.Visible; _tasksExpander.IsExpanded = true; }
                    return result;
                }, ConfirmAsync, message => { _status.Text = message.Length > 150 ? message.Substring(0, 150) : message; }, _running.Token, !test, ToolTrace,
                    partial => { if (!string.IsNullOrWhiteSpace(partial)) _status.Text = "回答中… " + (partial.Length > 160 ? partial.Substring(partial.Length - 160) : partial); },
                    _explicitScope);
                if (!test) PaneState.EnsureProject(expectedProject, Project.Current?.URI ?? "");
                if (!test) PaneState.EnsureMap(expectedMap, MapView.Active?.Map?.URI ?? "");
                if (generation != _projectGeneration) throw new OperationCanceledException("工程已切换。");
                Append("助手：" + answer);
                if (!test && sentPromptVersion == _promptEditVersion) _prompt.Clear();
                _status.Text = "完成";
            }
            catch (OperationCanceledException)
            {
                ledgerStatus = "cancelled";
                Append("请求已取消或等待模型超时；已完成操作仍保留，可查询任务状态。"); _status.Text = "已停止";
            }
            catch (Exception ex)
            {
                ledgerStatus = "failed"; ledgerError = ex.Message;
                Append("请求失败：" + ex.Message); _status.Text = "请求失败";
            }
            finally
            {
                WriteAiLedger(ledgerStatus, ledgerError, _explicitScope, test,
                    (runClient ?? _client).LastStats);
                _running.Dispose(); _running = null; _timer.Interval = TimeSpan.FromSeconds(5); SetBusy(false);
            }
        }

        /// <summary>写 AI 运行账本（JSONL，与 GP 账本同目录不同文件）；失败不影响对话。</summary>
        private void WriteAiLedger(string status, string error, ModelClient.ToolScope scope, bool isTest,
            ModelClient.RunStats stats)
        {
            try
            {
                var directory = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SamplingAssistant");
                Directory.CreateDirectory(directory);
                // 保留原始调用序列（含重复次数），另给去重名便于检索
                var names = (stats.ToolNames ?? new System.Collections.Generic.List<string>())
                    .Select(name => (JsonNode)name).ToArray();
                var uniqueNames = stats.ToolNames.Distinct().Select(name => (JsonNode)name).ToArray();
                var entry = new JsonObject
                {
                    ["ts"] = DateTime.Now.ToString("yyyy-MM-ddTHH:mm:sszzz"),
                    ["action"] = isTest ? "chat_test" : "chat",
                    ["scope"] = scope.ToString().ToLowerInvariant(),
                    ["status"] = status,
                    ["turns"] = stats.Turns,
                    ["tool_calls"] = stats.ToolCalls,
                    ["tool_calls_failed"] = stats.FailedCalls,
                    ["tool_names"] = new JsonArray(names),
                    ["tool_names_unique"] = new JsonArray(uniqueNames),
                    ["prompt_tokens"] = stats.PromptTokens,
                    ["completion_tokens"] = stats.CompletionTokens,
                    ["total_tokens"] = stats.TotalTokens,
                    ["finish_reason"] = stats.FinishReason,
                    ["elapsed_ms"] = stats.ElapsedMs,
                    ["error"] = (error ?? "").Length > 300 ? error.Substring(0, 300) : (error ?? "")
                };
                File.AppendAllText(Path.Combine(directory, "ai-ledger.jsonl"),
                    entry.ToJsonString() + Environment.NewLine);
            }
            catch (Exception ex)
            {
                // 账本是旁路：失败只留一行诊断，不打断对话
                Trace.WriteLine("AI 运行账本写入失败：" + ex.Message);
            }
        }
        private Task<bool> ConfirmAsync(string name, JsonObject input)
        {
            var description = (string)ToolProtocol.Tool(name)["description"];
            return Task.FromResult(UiConfirmWindow.Ask("确认地图操作：" + name, description + "\n\n执行这项操作？",
                input.ToJsonString(), "执行"));
        }

        private void StartPolling()
        {
            if (_timer.IsEnabled) return;
            _refreshLifetime?.Dispose(); _refreshLifetime = new CancellationTokenSource(); _timer.Start();
        }
        private void StopPolling() { _timer.Stop(); _refreshLifetime?.Cancel(); }

        private async Task RefreshAsync()
        {
            if (_refreshBusy || _refreshLifetime == null || _refreshLifetime.IsCancellationRequested) return;
            _refreshBusy = true;
            var lifetime = _refreshLifetime.Token;
            try
            {
                var current = await MapActions.ExecuteAsync("get_status", new JsonObject(), lifetime);
                lifetime.ThrowIfCancellationRequested();
                var project = (string)current["project"] ?? "";
                if (_project != project)
                {
                    _project = project; _projectGeneration++; _running?.Cancel(); _client.Clear(); _pane.Reset();
                    _openOutput.IsEnabled = _openFolder.IsEnabled = false;
                    _jobs.ItemsSource = null; _taskStatus.Text = "没有地理处理任务";
                }
                var mapUri = MapView.Active?.Map?.URI ?? "";
                if (!_changingMap && _map != mapUri)
                {
                    _map = mapUri; _projectGeneration++; _running?.Cancel(); _client.Clear();
                }
                _projectLabel.Text = "当前工程：" + (project.Length == 0 ? "尚未打开" : Path.GetFileName(project)) +
                    "\n地图：" + ((string)current["map"] ?? "") + "　进程：" + current["process_id"];
                UpdateConnectionPill();
                var previousOutput = _pane.OutputPath;
                var changed = await _pane.RefreshJobsAsync(token => MapActions.ExecuteAsync("list_jobs", new JsonObject(), token, project), lifetime);
                _openOutput.IsEnabled = _openFolder.IsEnabled = !string.IsNullOrEmpty(_pane.OutputPath) && File.Exists(_pane.OutputPath);
                if (_pane.OutputPath != previousOutput && _openOutput.IsEnabled) _tasksExpander.IsExpanded = true;
                _tasksExpander.Header = "任务与导出（" + _pane.Jobs.Count + "）" + (_openOutput.IsEnabled ? " · 有导出" : "");
                _tasksExpander.Visibility = _pane.Jobs.Count > 0 || _openOutput.IsEnabled ? Visibility.Visible : Visibility.Collapsed;
                if (changed)
                {
                    var selected = (_jobs.SelectedItem as JobChoice)?.Id;
                    var known = _jobs.Items.Cast<JobChoice>().Select(c => c.Id).ToHashSet();
                    var choices = _pane.Jobs.Select(node => new JobChoice(node)).ToList();
                    if (choices.Any(c => !known.Contains(c.Id) && PaneState.CanCancelJob(c.Snapshot, project))) _tasksExpander.IsExpanded = true;
                    _updatingJobs = true;
                    try
                    {
                        _jobs.ItemsSource = choices;
                        _jobs.SelectedItem = choices.FirstOrDefault(c => c.Id == selected) ?? choices.FirstOrDefault();
                    }
                    finally { _updatingJobs = false; }
                }
                ShowSelectedJob();
            }
            catch (OperationCanceledException) { }
            catch (Exception ex) { _taskStatus.Text = "刷新失败：" + ex.Message; }
            finally { _refreshBusy = false; }
        }

        private void ShowSelectedJob()
        {
            var jobId = (_jobs.SelectedItem as JobChoice)?.Id;
            var detail = _jobs.SelectedItem is JobChoice selected ? selected.Detail : "没有地理处理任务；模型提交分析后会自动显示在这里。";
            if (_taskStatus.Text != detail || _detailJobId != jobId)
            {
                var same = _detailJobId == jobId;
                var offset = same ? _taskStatus.VerticalOffset : 0;
                var start = same ? _taskStatus.SelectionStart : 0; var length = same ? _taskStatus.SelectionLength : 0;
                _taskStatus.Text = detail; _taskStatus.Select(Math.Min(start, detail.Length), Math.Min(length, Math.Max(0, detail.Length - start)));
                _taskStatus.ScrollToVerticalOffset(offset);
                _detailJobId = jobId;
            }
            _jobs.IsEnabled = _jobs.Items.Count > 0;
            _cancelJob.IsEnabled = !_jobActionBusy && _jobs.SelectedItem is JobChoice pending &&
                PaneState.CanCancelJob(pending.Snapshot, Project.Current?.URI ?? "");
            _loadJobOutput.IsEnabled = !_jobActionBusy && _jobs.SelectedItem is JobChoice job && job.CanLoad &&
                string.Equals(job.Project, Project.Current?.URI, StringComparison.OrdinalIgnoreCase) && MapView.Active?.Map != null;
        }

        private async Task DiagnoseAsync()
        {
            try
            {
                await RefreshAsync();
                var home = Environment.GetEnvironmentVariable("DSH_HOME") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".dsh");
                var report = PaneState.Diagnose(home, Bridge.IsListening, Process.GetCurrentProcess().Id, Project.Current?.URI ?? "");
                var config = (string)report["dsh_configuration"] == "saved" ? "已保存" : "未找到";
                var running = (string)report["dsh_process"] == "running" ? "正在运行" : (string)report["dsh_process"] == "stopped" ? "未运行" : "未知";
                Append("连接诊断\nPro 桥接：" + (Bridge.IsListening ? "可用" : "正在启动") + "\n" + _projectLabel.Text +
                    "\nDSH 配置：" + config + "；进程：" + running + "\nDSH 模型连接需在 DSH 中调用 get_status 验证。多开 Pro 可调用 list_pro_sessions、select_pro_session 选择工程。");
                _status.Text = "连接诊断完成";
            }
            catch (Exception ex) { _status.Text = "连接诊断失败：" + ex.Message; }
        }

        private void OpenOutput(bool folder)
        {
            try
            {
                var path = _pane.OutputPath;
                if (string.IsNullOrEmpty(path) || !File.Exists(path)) throw new InvalidOperationException("导出文件已移动或删除，请重新导出。");
                Process.Start(new ProcessStartInfo(folder ? Path.GetDirectoryName(path) : path) { UseShellExecute = true });
            }
            catch (Exception ex) { _status.Text = "无法打开：" + ex.Message; }
        }

        /// <summary>一张工具调用卡：标题行（状态点 + 工具名 + 阶段标签）+ 可展开的原始参数/结果。</summary>
        private sealed class ToolCardView
        {
            internal FrameworkElement Root;
            internal TextBox Body;
            internal TextBlock PhaseLabel;
            internal Border PhaseDot;
        }

        private sealed class JobChoice
        {
            internal string Id { get; }
            internal string Detail { get; }
            internal string Project { get; }
            internal bool CanLoad { get; }
            internal JsonNode Snapshot { get; }
            private readonly string _label;
            internal JobChoice(JsonNode node)
            {
                Snapshot = ToolProtocol.Copy(node);
                Id = (string)node["job_id"];
                Project = (string)node["project"] ?? "";
                CanLoad = (bool?)node["can_load_output"] == true;
                var state = (string)node["status"];
                var label = state switch { "queued" => "等待中", "running" => "运行中", "succeeded" => "成功", "failed" => "失败", "cancelled" => "已取消", _ => state };
                if ((state == "queued" || state == "running") && (bool?)node["cancel_requested"] == true) label = "已请求取消，等待结束";
                _label = (string)node["tool"] + " — " + label;
                Detail = _label + "\n" + ((string)node["value"] ?? "") + "\n" + ((string)node["messages"] ?? "");
                string Time(string key) => DateTimeOffset.TryParse((string)node[key], out var time) ? time.ToLocalTime().ToString("MM-dd HH:mm:ss") : "—";
                Detail += "\n开始：" + Time("started_at") + "　结束：" + Time("finished_at") +
                    "\n耗时：" + (((long?)node["elapsed_ms"] ?? 0) / 1000.0).ToString("0.0") + " 秒" +
                    "\n输出：" + ((string)node["output_path"] ?? "无成功输出");
            }
            public override string ToString() => _label;
        }
    }
}
