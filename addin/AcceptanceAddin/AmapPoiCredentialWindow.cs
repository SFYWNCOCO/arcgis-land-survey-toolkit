using System;
using System.Windows;
using System.Windows.Controls;
using AcceptanceAddin.Ui;
using UiKit = AcceptanceAddin.Ui.Ui;

namespace AcceptanceAddin
{
    internal sealed class AmapPoiCredentialWindow : Window
    {
        private readonly PasswordBox _key = new PasswordBox { Margin = new Thickness(0, 6, 0, 10), MinWidth = 360 };
        private readonly TextBlock _status = new TextBlock { Margin = new Thickness(0, 4, 0, 8), Style = UiTheme.CaptionStyle };

        private AmapPoiCredentialWindow()
        {
            Title = "高德 POI 服务配置";
            Width = 460;
            SizeToContent = SizeToContent.Height;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            ResizeMode = ResizeMode.NoResize;

            var root = new StackPanel();
            UiTheme.Apply(root);
            UiTheme.WatchStatus(_status);
            Background = UiTheme.Brush(UiTheme.Page);
            root.Children.Add(UiKit.Section("高德 POI 服务配置"));
            root.Children.Add(UiKit.Caption("填写高德开放平台申请的 Web 服务 API Key。密钥仅保存在本机当前 Windows 用户的 DPAPI 加密文件中；检索时不会作为地理处理参数或运行日志输出。在线检索会把范围包络坐标、关键词/类型码和 Key 发给高德；原始项目面只在本机做精确筛选。"));
            root.Children.Add(new TextBlock { Text = "Web 服务 API Key", Margin = new Thickness(0, 6, 0, 0), FontSize = 11, Foreground = UiTheme.Brush(UiTheme.TextSecondary) });
            root.Children.Add(_key);
            root.Children.Add(UiKit.Caption("POI 坐标来自高德 GCJ-02；转入图层时会近似反算到 WGS84，仅作位置参考。"));
            root.Children.Add(_status);

            var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 8, 0, 0) };
            var save = new Button { Content = "加密保存", MinWidth = 88, Margin = new Thickness(0, 0, 8, 0) };
            UiTheme.Primary(save);
            save.Click += (_, __) => SaveKey();
            var forget = new Button { Content = "删除本机密钥", Margin = new Thickness(0, 0, 8, 0) };
            UiTheme.DangerApply(forget);
            forget.Click += (_, __) => DeleteKey();
            var close = new Button { Content = "关闭", MinWidth = 72 };
            close.Click += (_, __) => Close();
            actions.Children.Add(save);
            actions.Children.Add(forget);
            actions.Children.Add(close);
            root.Children.Add(actions);
            Content = new Border
            {
                Background = UiTheme.Brush(UiTheme.Page),
                Padding = new Thickness(18),
                Child = UiKit.Card(root, null, null)
            };

            try { _key.Password = ServiceCredentials.LoadAmapPoi() ?? ""; }
            catch { _status.Text = "原密钥无法解密，请重新填写并保存。"; }
        }

        internal static void Open() => new AmapPoiCredentialWindow().ShowDialog();

        private void SaveKey()
        {
            try
            {
                ServiceCredentials.SaveAmapPoi(_key.Password);
                _status.Text = "密钥已使用 Windows 当前用户 DPAPI 加密保存。";
            }
            catch (Exception ex) { _status.Text = "保存失败：" + ex.Message; }
        }

        private void DeleteKey()
        {
            if (!UiConfirmWindow.Ask("删除本机密钥", "将删除本机保存的高德 POI Web 服务密钥，检索前需要重新填写。", null, "删除")) return;
            try
            {
                ServiceCredentials.DeleteAmapPoi();
                _key.Clear();
                _status.Text = "已删除本机保存的高德密钥。";
            }
            catch (Exception ex) { _status.Text = "删除失败：" + ex.Message; }
        }
    }
}
