// 视觉系统之上的基础组件：卡片、分区标题、顶部品牌区、状态胶囊、气泡、确认弹窗。
// 只依赖 WPF，可被 Pro 插件与离线预览程序共同引用。
using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;

namespace AcceptanceAddin.Ui
{
    internal static class Ui
    {
        internal static readonly Thickness CardPadding = new Thickness(Space.Lg, Space.Md, Space.Lg, Space.Md);

        internal static SolidColorBrush ToneBrush(Tone tone)
        {
            switch (tone)
            {
                case Tone.Accent: return UiTheme.Brush(UiTheme.Accent);
                case Tone.Success: return UiTheme.Brush(UiTheme.Success);
                case Tone.Warning: return UiTheme.Brush(UiTheme.Warning);
                case Tone.Danger: return UiTheme.Brush(UiTheme.Danger);
                case Tone.Info: return UiTheme.Brush(UiTheme.Info);
                default: return UiTheme.Brush(UiTheme.TextMuted);
            }
        }

        internal static SolidColorBrush ToneSoftBrush(Tone tone)
        {
            var color = ToneBrush(tone).Color;
            var alpha = UiTheme.Dark ? (byte)0x2E : (byte)0x1A;
            return UiTheme.Brush(Color.FromArgb(alpha, color.R, color.G, color.B));
        }

        // ---------- 容器 ----------

        internal static Border Card(UIElement content, string title = null, string caption = null)
        {
            var stack = new StackPanel();
            if (!string.IsNullOrEmpty(title))
            {
                stack.Children.Add(Title(title));
                if (!string.IsNullOrEmpty(caption)) stack.Children.Add(Caption(caption));
            }
            if (content != null) stack.Children.Add(content);
            return new Border
            {
                Background = UiTheme.Brush(UiTheme.Surface),
                BorderBrush = UiTheme.Brush(UiTheme.Border),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = CardPadding,
                Margin = Space.Bottom(Space.Sm),
                SnapsToDevicePixels = true,
                Child = stack
            };
        }

        internal static Border Surface(UIElement content, Thickness padding, double radius = 6, bool border = true)
            => new Border
            {
                Background = UiTheme.Brush(UiTheme.Surface),
                BorderBrush = UiTheme.Brush(UiTheme.Border),
                BorderThickness = border ? new Thickness(1) : new Thickness(0),
                CornerRadius = new CornerRadius(radius),
                Padding = padding,
                SnapsToDevicePixels = true,
                Child = content
            };

        internal static Border FooterBar(UIElement content)
            => new Border
            {
                Background = UiTheme.Brush(UiTheme.Surface),
                BorderBrush = UiTheme.Brush(UiTheme.Border),
                BorderThickness = new Thickness(0, 1, 0, 0),
                Padding = new Thickness(Space.Md, Space.Sm, Space.Md, Space.Sm),
                Child = content
            };

        // ---------- 文字 ----------

        internal static TextBlock Title(string text) => new TextBlock
        {
            Text = text,
            Style = UiTheme.SectionTitleStyle,
            TextWrapping = TextWrapping.Wrap
        };

        internal static TextBlock Caption(string text) => new TextBlock
        {
            Text = text,
            Style = UiTheme.CaptionStyle,
            Margin = new Thickness(0, 0, 0, 6)
        };

        internal static TextBlock Small(string text) => new TextBlock
        {
            Text = text,
            Style = UiTheme.SmallStyle,
            TextWrapping = TextWrapping.Wrap
        };

        internal static FrameworkElement Section(string text)
        {
            var bar = new Border
            {
                Width = 3,
                Height = 13,
                CornerRadius = new CornerRadius(2),
                Background = UiTheme.Brush(UiTheme.Accent),
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 7, 0)
            };
            var title = new TextBlock
            {
                Text = text,
                Style = UiTheme.SectionTitleStyle,
                VerticalAlignment = VerticalAlignment.Center
            };
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 2, 0, 6) };
            row.Children.Add(bar);
            row.Children.Add(title);
            return row;
        }

        // ---------- 顶部标题条 ----------

        /// <summary>扁平标题条：标题 + 说明 + 右侧版本，底部细线收口。不做渐变/阴影，贴近桌面软件原生头部。</summary>
        internal static Border PageHeader(string title, string subtitle, string rightText = null)
        {
            var left = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            left.Children.Add(new TextBlock
            {
                Text = title,
                FontSize = 13.5,
                FontWeight = FontWeights.SemiBold,
                Foreground = UiTheme.Brush(UiTheme.TextPrimary)
            });
            if (!string.IsNullOrEmpty(subtitle))
                left.Children.Add(new TextBlock
                {
                    Text = subtitle,
                    FontSize = 10.5,
                    Foreground = UiTheme.Brush(UiTheme.TextMuted),
                    Margin = new Thickness(0, 1, 0, 0),
                    TextWrapping = TextWrapping.Wrap
                });
            var grid = new Grid();
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.Children.Add(left);
            if (!string.IsNullOrEmpty(rightText))
            {
                var version = new TextBlock
                {
                    Text = rightText,
                    FontSize = 10,
                    FontFamily = new FontFamily("Cascadia Mono, Consolas"),
                    Foreground = UiTheme.Brush(UiTheme.TextMuted),
                    VerticalAlignment = VerticalAlignment.Center,
                    Margin = new Thickness(8, 0, 0, 0)
                };
                Grid.SetColumn(version, 1);
                grid.Children.Add(version);
            }
            return new Border
            {
                Background = UiTheme.Brush(UiTheme.Page),
                BorderBrush = UiTheme.Brush(UiTheme.Border),
                BorderThickness = new Thickness(0, 0, 0, 1),
                Padding = new Thickness(0, 0, 0, 9),
                Margin = new Thickness(0, 0, 0, 2),
                Child = grid
            };
        }

        internal static DropShadowEffect Shadow(double opacity)
            => new DropShadowEffect
            {
                BlurRadius = 14,
                ShadowDepth = 2,
                Direction = 270,
                Opacity = opacity,
                Color = Colors.Black,
                RenderingBias = RenderingBias.Performance
            };

        // ---------- 状态与标签 ----------

        internal static Border Pill(string text, Tone tone = Tone.Neutral)
            => new Border
            {
                Background = ToneSoftBrush(tone),
                CornerRadius = new CornerRadius(3),
                Padding = new Thickness(9, 3, 9, 3),
                HorizontalAlignment = HorizontalAlignment.Left,
                Child = new TextBlock
                {
                    Text = text,
                    FontSize = 10.5,
                    FontWeight = FontWeights.SemiBold,
                    Foreground = ToneBrush(tone)
                }
            };

        internal static Border Dot(Color color, double size = 8)
            => new Border
            {
                Width = size,
                Height = size,
                CornerRadius = new CornerRadius(size / 2),
                Background = UiTheme.Brush(color),
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 7, 0)
            };

        internal static Button Chip(string text, Action click = null)
        {
            var button = new Button { Content = text, Margin = new Thickness(0, 0, 4, 0) };
            UiTheme.Chip(button);
            if (click != null) button.Click += (_, __) => click();
            return button;
        }

        internal static Border Divider()
            => new Border
            {
                Height = 1,
                Background = UiTheme.Brush(UiTheme.Border),
                Margin = new Thickness(0, 8, 0, 8)
            };

        internal static Border EmptyState(string title, string hint, UIElement action = null)
        {
            var stack = new StackPanel { Margin = new Thickness(2, 10, 2, 10), HorizontalAlignment = HorizontalAlignment.Left };
            stack.Children.Add(new TextBlock
            {
                Text = title,
                FontSize = 11.5,
                FontWeight = FontWeights.SemiBold,
                Foreground = UiTheme.Brush(UiTheme.TextSecondary)
            });
            stack.Children.Add(new TextBlock
            {
                Text = hint,
                FontSize = 10.5,
                Foreground = UiTheme.Brush(UiTheme.TextMuted),
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 3, 0, 0)
            });
            if (action != null)
            {
                action.SetValue(FrameworkElement.MarginProperty, new Thickness(0, 8, 0, 0));
                action.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Left);
                stack.Children.Add(action);
            }
            return new Border { Background = Brushes.Transparent, Child = stack };
        }

        // ---------- 对话 ----------

        internal static Border Bubble(string text, bool user)
        {
            var body = new TextBlock
            {
                Text = text ?? "",
                TextWrapping = TextWrapping.Wrap,
                FontSize = 11.5,
                LineHeight = 18,
                Foreground = UiTheme.Brush(UiTheme.TextPrimary)
            };
            if (!user)
                return new Border
                {
                    Background = Brushes.Transparent,
                    BorderThickness = new Thickness(0),
                    Padding = new Thickness(0),
                    Margin = new Thickness(0, 2, 0, 2),
                    HorizontalAlignment = HorizontalAlignment.Left,
                    Child = body
                };
            // 用户指令做成整行引述块（左细线 + 浅底），不再用右侧聊天气泡，弱化"聊天工具"观感。
            var label = new TextBlock
            {
                Text = "你",
                FontSize = 9.5,
                Foreground = UiTheme.Brush(UiTheme.TextSecondary),
                Margin = new Thickness(0, 0, 0, 3)
            };
            var stack = new StackPanel();
            stack.Children.Add(label);
            stack.Children.Add(body);
            return new Border
            {
                Background = UiTheme.Brush(UiTheme.SurfaceAlt),
                BorderBrush = UiTheme.Brush(UiTheme.BorderStrong),
                BorderThickness = new Thickness(2, 0, 0, 0),
                Padding = new Thickness(8, 6, 8, 6),
                Margin = new Thickness(0, 5, 0, 4),
                HorizontalAlignment = HorizontalAlignment.Stretch,
                Child = stack
            };
        }

        internal static Border AssistantBubble(string text)
        {
            var head = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 3) };
            head.Children.Add(new TextBlock
            {
                Text = "助手",
                FontSize = 10,
                FontWeight = FontWeights.SemiBold,
                Foreground = UiTheme.Brush(UiTheme.TextMuted),
                VerticalAlignment = VerticalAlignment.Center
            });
            var bubble = Bubble(text, false);
            bubble.HorizontalAlignment = HorizontalAlignment.Left;
            var wrap = new StackPanel { Margin = new Thickness(0, 3, 0, 2) };
            wrap.Children.Add(head);
            wrap.Children.Add(bubble);
            return new Border { Background = Brushes.Transparent, Child = wrap };
        }

        internal static Border SystemNote(string text)
            => new Border
            {
                Margin = new Thickness(0, 6, 0, 6),
                Child = new TextBlock
                {
                    Text = text,
                    FontSize = 10.5,
                    Foreground = UiTheme.Brush(UiTheme.TextMuted),
                    TextWrapping = TextWrapping.Wrap,
                    HorizontalAlignment = HorizontalAlignment.Center,
                    TextAlignment = TextAlignment.Center
                }
            };

        /// <summary>可选中复制的系统注记（工具回执、错误原文、诊断信息），保持居中弱化外观。</summary>
        internal static Border SelectableNote(string text)
        {
            var box = new TextBox
            {
                Text = text ?? "",
                IsReadOnly = true,
                AcceptsReturn = true,
                TextWrapping = TextWrapping.Wrap,
                BorderThickness = new Thickness(0),
                Background = Brushes.Transparent,
                Padding = new Thickness(8, 4, 8, 4),
                Margin = new Thickness(0),
                FontSize = 10.5,
                Foreground = UiTheme.Brush(UiTheme.TextMuted),
                TextAlignment = TextAlignment.Center,
                MaxHeight = 180,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto
            };
            return new Border { Margin = new Thickness(0, 4, 0, 4), Child = box };
        }

        // ---------- 工具调用卡片 ----------

        internal static Border ToolCard(string name, out TextBlock phaseLabel, out Border phaseDot)
        {
            var dot = new Border
            {
                Width = 8,
                Height = 8,
                CornerRadius = new CornerRadius(4),
                Background = UiTheme.Brush(UiTheme.Warning),
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 7, 0)
            };
            phaseDot = dot;
            phaseLabel = new TextBlock
            {
                Text = "执行中",
                FontSize = 10.5,
                FontWeight = FontWeights.SemiBold,
                Foreground = UiTheme.Brush(UiTheme.TextMuted),
                VerticalAlignment = VerticalAlignment.Center
            };
            var row = new Grid();
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var left = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            left.Children.Add(dot);
            left.Children.Add(new TextBlock
            {
                Text = name,
                FontSize = 11.5,
                FontWeight = FontWeights.SemiBold,
                Foreground = UiTheme.Brush(UiTheme.TextPrimary),
                VerticalAlignment = VerticalAlignment.Center
            });
            row.Children.Add(left);
            Grid.SetColumn(phaseLabel, 1);
            row.Children.Add(phaseLabel);
            return new Border
            {
                Background = Brushes.Transparent,
                BorderBrush = UiTheme.Brush(UiTheme.Border),
                BorderThickness = new Thickness(0, 0, 0, 1),
                Padding = new Thickness(2, 5, 2, 5),
                Margin = new Thickness(0, 1, 0, 0),
                Child = row
            };
        }

        internal static void SetPhase(Tone tone, TextBlock phaseLabel, Border phaseDot, string text)
        {
            if (phaseLabel != null)
            {
                phaseLabel.Text = text;
                phaseLabel.Foreground = ToneBrush(tone);
            }
            if (phaseDot != null) phaseDot.Background = ToneBrush(tone);
        }

        internal static TextBlock Readout(string text) => new TextBlock
        {
            Text = text,
            FontFamily = new FontFamily("Cascadia Mono, Consolas"),
            FontSize = 10.5,
            Foreground = UiTheme.Brush(UiTheme.TextSecondary),
            TextWrapping = TextWrapping.Wrap
        };
    }

    /// <summary>与整体风格一致的确认弹窗，替代系统 MessageBox，用于 AI 地图操作的人工确认。</summary>
    internal static class UiConfirmWindow
    {
        internal static bool Ask(string title, string message, string detail, string accept = "执行")
        {
            try
            {
                var window = new Window
                {
                    Title = title,
                    WindowStyle = WindowStyle.None,
                    ResizeMode = ResizeMode.NoResize,
                    SizeToContent = SizeToContent.Height,
                    Width = 440,
                    WindowStartupLocation = WindowStartupLocation.CenterOwner,
                    Background = Brushes.Transparent,
                    AllowsTransparency = true,
                    ShowInTaskbar = false,
                    Owner = Application.Current?.MainWindow
                };
                var result = false;
                var stack = new StackPanel();
                stack.Children.Add(Ui.Section(title));
                stack.Children.Add(new TextBlock
                {
                    Text = message,
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = UiTheme.Brush(UiTheme.TextPrimary),
                    FontSize = 12,
                    LineHeight = 19
                });
                if (!string.IsNullOrWhiteSpace(detail))
                {
                    var box = new TextBox
                    {
                        Text = detail,
                        IsReadOnly = true,
                        TextWrapping = TextWrapping.Wrap,
                        AcceptsReturn = true,
                        MaxHeight = 150,
                        VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                        Background = UiTheme.Brush(UiTheme.SurfaceAlt),
                        BorderThickness = new Thickness(0),
                        Padding = new Thickness(9),
                        Margin = new Thickness(0, 10, 0, 0),
                        FontFamily = new FontFamily("Cascadia Mono, Consolas"),
                        FontSize = 10.5
                    };
                    stack.Children.Add(box);
                }
                var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 14, 0, 0) };
                var cancel = new Button { Content = "取消", Margin = new Thickness(0, 0, 7, 0), MinWidth = 74 };
                cancel.Click += (_, __) => { window.DialogResult = false; window.Close(); };
                var ok = new Button { Content = accept, MinWidth = 88 };
                UiTheme.Primary(ok);
                ok.Click += (_, __) => { result = true; window.DialogResult = true; window.Close(); };
                buttons.Children.Add(cancel);
                buttons.Children.Add(ok);
                stack.Children.Add(buttons);
                var card = new Border
                {
                    Background = UiTheme.Brush(UiTheme.Surface),
                    BorderBrush = UiTheme.Brush(UiTheme.Border),
                    BorderThickness = new Thickness(1),
                    CornerRadius = new CornerRadius(8),
                    Padding = new Thickness(16),
                    Effect = Ui.Shadow(0.2),
                    Child = stack
                };
                UiTheme.Apply(card);
                window.Content = card;
                window.KeyDown += (_, e) => { if (e.Key == Key.Escape) { window.DialogResult = false; window.Close(); } };
                window.Loaded += (_, __) => ok.Focus();
                window.ShowDialog();
                return result;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Trace.WriteLine("确认弹窗失败，退回系统对话框：" + ex.Message);
                return MessageBox.Show(message + (string.IsNullOrWhiteSpace(detail) ? "" : "\n\n" + detail), title,
                    MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No) == MessageBoxResult.Yes;
            }
        }
    }
}
