using System;
using System.Linq;
using System.Reflection;
using System.Windows;
using System.Windows.Controls;
using AcceptanceAddin.Ui;
using UiKit = AcceptanceAddin.Ui.Ui;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;

namespace AcceptanceAddin
{
    // One DockPane owns each workflow view once; changing tabs preserves drafts/tasks.
    internal sealed class AcceptanceWorkspacePane : DockPane
    {
        internal const string PaneId = "AcceptanceAddin_Workspace";
        private static AcceptanceWorkspacePane _instance;
        private readonly TabControl _tabs = new TabControl();
        private readonly TabItem _assistantTab = new TabItem { Header = "AI 助手" };
        private readonly TabItem _samplingTab = new TabItem { Header = "采样与出图" };
        private Control _root;
        private string _page = "sampling";
        private bool _created;
        private ProAiBridge.ChatPane _assistant;
        internal SamplingWizardPane Sampling { get; private set; }
        internal static SamplingWizardPane ExistingSampling => _instance?.Sampling;

        internal static object Snapshot() => new {
            registered = true, opened = _instance?._created ?? false,
            page = _instance?._created == true ? (_instance._tabs.SelectedItem == _instance._samplingTab ? "sampling" : "assistant") : _instance?._page ?? "assistant",
            requested_page = _instance?._page ?? "assistant",
            assistant_draft_chars = _instance?._assistant?.DraftLength ?? 0,
            sampling_project_name_chars = _instance?.Sampling?.ProjectNameLength ?? 0,
            assistant_created = _instance?._assistant != null,
            sampling_created = _instance?.Sampling != null,
            sampling_busy = _instance?.Sampling?.IsBusy ?? false,
            task = WorkbenchJobService.Snapshot()
        };

        internal static object Show(string page = "assistant")
        {
            if (page != "assistant" && page != "sampling") throw new ArgumentException("工作台页面须为assistant或sampling。");
            var pane = FrameworkApplication.DockPaneManager.Find(PaneId) as AcceptanceWorkspacePane
                ?? throw new InvalidOperationException("验收工作台未注册，请重新安装统一插件。");
            _instance = pane;
            pane._page = page;
            if (pane._created) pane._tabs.SelectedItem = page == "sampling" ? pane._samplingTab : pane._assistantTab;
            pane.Activate();
            return Snapshot();
        }

        protected override Control OnCreateContent()
        {
            if (_created) return _root;
            _instance = this; _created = true;
            ApplyTabStyle();
            var header = UiKit.PageHeader("补充耕地验收工作台", "AI 助手 · 采样布点 · 出图交付 · MCP 桥接", "v" + AddInVersion());
            header.Margin = new Thickness(0, 0, 0, 8);
            var tabs = _tabs;
            tabs.Margin = new Thickness(0);
            tabs.Items.Add(_assistantTab); tabs.Items.Add(_samplingTab);
            tabs.SelectionChanged += (_, e) => {
                if (e.Source != tabs) return;
                _page = tabs.SelectedItem == _samplingTab ? "sampling" : "assistant";
                EnsureSelectedView();
            };
            tabs.SelectedItem = _page == "sampling" ? _samplingTab : _assistantTab;
            EnsureSelectedView();
            var root = new DockPanel { Margin = new Thickness(12, 10, 12, 0), LastChildFill = true };
            DockPanel.SetDock(header, Dock.Top);
            root.Children.Add(header);
            root.Children.Add(tabs);
            var host = new UserControl { Content = root };
            UiTheme.Apply(host);
            _root = host;
            return _root;
        }

        private void ApplyTabStyle()
        {
            if (UiTheme.TabControlImplicit != null) _tabs.Style = UiTheme.TabControlImplicit;
            if (UiTheme.TabItemImplicit != null)
            {
                _assistantTab.Style = UiTheme.TabItemImplicit;
                _samplingTab.Style = UiTheme.TabItemImplicit;
            }
        }

        private static string AddInVersion()
        {
            try
            {
                var assembly = typeof(AcceptanceWorkspacePane).Assembly;
                var informational = assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
                if (!string.IsNullOrWhiteSpace(informational)) return informational.Split('+')[0];
                var version = assembly.GetName().Version;
                return version == null ? "" : version.ToString(3);
            }
            catch { return ""; }
        }

        private void EnsureSelectedView()
        {
            if (_page == "sampling")
            {
                if (Sampling == null) { Sampling = new SamplingWizardPane(); _samplingTab.Content = Sampling; }
            }
            else if (_assistant == null) { _assistant = new ProAiBridge.ChatPane(); _assistantTab.Content = _assistant; }
        }
    }
}
