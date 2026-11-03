// 统一视觉系统：色板、字阶、控件样式与基础组件。
// 设计约束：全部用代码/字符串模板构建（插件无 XAML 文件）；主题按 ArcGIS Pro 明暗主题自动切换；
// XAML 模板解析失败时静默退回系统默认样式，保证侧栏不会因样式问题无法打开。
using System;
using System.Reflection;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Documents;
using System.Windows.Markup;
using System.Windows.Media;

namespace AcceptanceAddin.Ui
{
    internal enum Tone { Neutral, Accent, Success, Warning, Danger, Info }

    internal static class UiTheme
    {
        internal static bool Dark { get; private set; }

        internal static Color Page, Surface, SurfaceAlt, Sunken, Border, BorderStrong, TextPrimary, TextSecondary,
            TextMuted, Accent, AccentHover, AccentSoft, OnAccent, Success, Warning, Danger, Info, Scroll, Overlay,
            Fill, FillHover, OnFill;

        internal static readonly FontFamily FontFamily = new FontFamily("Microsoft YaHei UI, Segoe UI, PingFang SC");

        internal static Style TextBlockImplicit, ButtonImplicit, TextBoxImplicit, PasswordImplicit, ComboImplicit,
            ComboItemImplicit, CheckImplicit, ExpanderImplicit, ListBoxItemImplicit, ScrollBarImplicit, ToolTipImplicit,
            TabControlImplicit, TabItemImplicit, AccentButtonStyle, QuietButtonStyle, DangerButtonStyle, ChipButtonStyle,
            SectionTitleStyle, CaptionStyle, MonoStyle, SmallStyle,
            ListViewImplicit, ListViewItemImplicit, GridViewHeaderImplicit, ExpanderFlatStyle;

        private static ResourceDictionary _dictionary;
        private static ControlTemplate _buttonTemplate, _textBoxTemplate, _passwordTemplate, _comboTemplate,
            _checkTemplate, _expanderTemplate, _tabItemTemplate, _tabControlTemplate, _chipTemplate,
            _scrollTemplate, _scrollHoriTemplate, _toolTipTemplate, _listBoxItemTemplate, _comboItemTemplate,
            _listViewItemTemplate, _gridHeaderTemplate, _expanderFlatTemplate;

        static UiTheme()
        {
            try
            {
                Dark = DetectDarkTheme();
                LoadPalette();
                BuildTemplates();
                BuildStyles();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Trace.WriteLine("UI 主题初始化失败：" + ex.Message);
            }
        }

        // ---------- 基础 ----------

        internal static SolidColorBrush Brush(Color c)
        {
            var brush = new SolidColorBrush(c);
            brush.Freeze();
            return brush;
        }

        private static Color C(string hex) => (Color)ColorConverter.ConvertFromString(hex);

        private static void LoadPalette()
        {
            if (Dark)
            {
                Page = C("#1C1E21"); Surface = C("#25272B"); SurfaceAlt = C("#2B2E33"); Sunken = C("#202225");
                Border = C("#383B40"); BorderStrong = C("#4A4E54");
                TextPrimary = C("#E8EAED"); TextSecondary = C("#B0B5BC"); TextMuted = C("#8A9099");
                Accent = C("#3FAF8E"); AccentHover = C("#4DBF9C"); AccentSoft = C("#1C2E29"); OnAccent = C("#06231A");
                Success = C("#4FB184"); Warning = C("#D89A4A"); Danger = C("#E8837B"); Info = C("#8FB3FF");
                Scroll = C("#4A5260"); Overlay = C("#FFFFFF");
                Fill = C("#1E6E58"); FillHover = C("#237A62"); OnFill = C("#F5FAF8");
            }
            else
            {
                Page = C("#F3F4F6"); Surface = C("#FFFFFF"); SurfaceAlt = C("#F7F8F9"); Sunken = C("#ECEEF0");
                Border = C("#DFE3E6"); BorderStrong = C("#C9CFD4");
                TextPrimary = C("#1F2328"); TextSecondary = C("#4B5563"); TextMuted = C("#667085");
                Accent = C("#0F5F4C"); AccentHover = C("#0B4F3F"); AccentSoft = C("#EDF3F1"); OnAccent = C("#FFFFFF");
                Success = C("#1B6E45"); Warning = C("#8A5A00"); Danger = C("#B42318"); Info = C("#1F5AA6");
                Scroll = C("#C7CDD6"); Overlay = C("#000000");
                Fill = C("#0F5F4C"); FillHover = C("#0B4F3F"); OnFill = C("#FFFFFF");
            }
        }

        /// <summary>读 ArcGIS Pro 当前应用主题；用反射避免样式系统依赖 Pro SDK（离线预览可复用）。</summary>
        private static bool DetectDarkTheme()
        {
            try
            {
                var forced = Environment.GetEnvironmentVariable("ACCEPTANCE_UI_THEME") ?? "";
                if (forced.Equals("dark", StringComparison.OrdinalIgnoreCase)) return true;
                if (forced.Equals("light", StringComparison.OrdinalIgnoreCase)) return false;
                var type = Type.GetType("ArcGIS.Desktop.Framework.FrameworkApplication, ArcGIS.Desktop.Framework");
                var prop = type?.GetProperty("ApplicationTheme", BindingFlags.Public | BindingFlags.Static);
                var value = prop?.GetValue(null);
                if (value == null) return false;
                var text = value.ToString() ?? "";
                // Pro 的主题对象可能不是枚举，而是带 Scheme/Name 的对象：把所有字符串/枚举属性拼起来判断
                foreach (var property in value.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
                {
                    try
                    {
                        if (property.GetIndexParameters().Length > 0) continue;
                        if (property.PropertyType == typeof(string))
                            text += " " + (property.GetValue(value) as string ?? "");
                        else if (property.PropertyType.IsEnum)
                            text += " " + property.GetValue(value);
                    }
                    catch { }
                }
                return text.IndexOf("Dark", StringComparison.OrdinalIgnoreCase) >= 0;
            }
            catch { return false; }
        }

        /// <summary>把视觉系统挂到控件根：字体、默认前景、背景与隐式控件样式。</summary>
        internal static void Apply(FrameworkElement host)
        {
            if (host == null) return;
            try
            {
                TextElement.SetFontFamily(host, FontFamily);
                TextElement.SetForeground(host, Brush(TextPrimary));
                TextOptions.SetTextFormattingMode(host, TextFormattingMode.Display);
                TextOptions.SetTextRenderingMode(host, TextRenderingMode.ClearType);
                if (host is Control control) control.Background = Brush(Page);
                else if (host is Panel panel) panel.Background = Brush(Page);
                if (_dictionary != null) host.Resources = _dictionary;
            }
            catch (Exception ex) { System.Diagnostics.Trace.WriteLine("UI 主题应用失败：" + ex.Message); }
        }

        // ---------- 模板 ----------

        private static void BuildTemplates()
        {
            _buttonTemplate = T(ButtonTemplateXaml);
            _textBoxTemplate = T(TextBoxTemplateXaml);
            _passwordTemplate = T(PasswordBoxTemplateXaml);
            _comboTemplate = T(ComboBoxTemplateXaml);
            _checkTemplate = T(CheckBoxTemplateXaml);
            _expanderTemplate = T(ExpanderTemplateXaml);
            _expanderFlatTemplate = T(ExpanderFlatTemplateXaml);
            _tabItemTemplate = T(TabItemTemplateXaml);
            _tabControlTemplate = T(TabControlTemplateXaml);
            _chipTemplate = T(ChipTemplateXaml);
            _scrollTemplate = T(ScrollBarTemplateXaml);
            _scrollHoriTemplate = T(ScrollBarHorizontalTemplateXaml);
            _toolTipTemplate = T(ToolTipTemplateXaml);
            _listBoxItemTemplate = T(ListBoxItemTemplateXaml);
            _comboItemTemplate = T(ComboBoxItemTemplateXaml);
            _listViewItemTemplate = T(ListViewItemTemplateXaml);
            _gridHeaderTemplate = T(GridViewColumnHeaderTemplateXaml);
        }

        private static ControlTemplate T(string xaml)
        {
            try { return XamlReader.Parse(Render(xaml)) as ControlTemplate; }
            catch (Exception ex)
            {
                System.Diagnostics.Trace.WriteLine("UI 模板解析失败：" + ex.Message);
                return null;
            }
        }

        private static string Hex(Color c) => "#" + c.R.ToString("X2") + c.G.ToString("X2") + c.B.ToString("X2");

        private static string Render(string xaml) => xaml
            .Replace("$PAGE$", Hex(Page)).Replace("$SURFACE$", Hex(Surface)).Replace("$SURFALT$", Hex(SurfaceAlt))
            .Replace("$SUNKEN$", Hex(Sunken)).Replace("$BORDER$", Hex(Border)).Replace("$BORDERSTRONG$", Hex(BorderStrong))
            .Replace("$TEXT$", Hex(TextPrimary)).Replace("$TEXT2$", Hex(TextSecondary)).Replace("$TEXTMUTED$", Hex(TextMuted))
            .Replace("$ACCENT$", Hex(Accent)).Replace("$ACCENTHOVER$", Hex(AccentHover)).Replace("$ACCENTSOFT$", Hex(AccentSoft))
            .Replace("$ONACCENT$", Hex(OnAccent)).Replace("$SCROLL$", Hex(Scroll)).Replace("$DANGER$", Hex(Danger))
            .Replace("$OVERLAY$", Hex(Overlay));

        private const string Head = "xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml'";

        private const string ButtonTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type Button}'>
  <Grid>
    <Border x:Name='Chrome' CornerRadius='5' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='{TemplateBinding BorderThickness}' SnapsToDevicePixels='True'/>
    <Border x:Name='Shade' CornerRadius='5' Background='$OVERLAY$' Opacity='0' IsHitTestVisible='False'/>
    <ContentPresenter x:Name='Body' Margin='{TemplateBinding Padding}' HorizontalAlignment='{TemplateBinding HorizontalContentAlignment}' VerticalAlignment='Center' RecognizesAccessKey='True' TextElement.Foreground='{TemplateBinding Foreground}'/>
  </Grid>
  <ControlTemplate.Triggers>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Shade' Property='Opacity' Value='0.06'/></Trigger>
    <Trigger Property='IsPressed' Value='True'><Setter TargetName='Shade' Property='Opacity' Value='0.12'/></Trigger>
    <Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='Chrome' Property='BorderBrush' Value='$ACCENT$'/></Trigger>
    <Trigger Property='IsEnabled' Value='False'><Setter Property='Opacity' Value='0.4'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string TextBoxTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type TextBox}'>
  <Border x:Name='Chrome' CornerRadius='5' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='{TemplateBinding BorderThickness}' SnapsToDevicePixels='True'>
    <ScrollViewer x:Name='PART_ContentHost' Margin='{TemplateBinding Padding}' Focusable='False'
                  HorizontalScrollBarVisibility='{TemplateBinding HorizontalScrollBarVisibility}'
                  VerticalScrollBarVisibility='{TemplateBinding VerticalScrollBarVisibility}'/>
  </Border>
  <ControlTemplate.Triggers>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Chrome' Property='BorderBrush' Value='$BORDERSTRONG$'/></Trigger>
    <Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='Chrome' Property='BorderBrush' Value='$ACCENT$'/></Trigger>
    <Trigger Property='IsEnabled' Value='False'><Setter Property='Opacity' Value='0.55'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string PasswordBoxTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type PasswordBox}'>
  <Border x:Name='Chrome' CornerRadius='5' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='{TemplateBinding BorderThickness}' SnapsToDevicePixels='True'>
    <ScrollViewer x:Name='PART_ContentHost' Margin='{TemplateBinding Padding}' Focusable='False' HorizontalScrollBarVisibility='Hidden' VerticalScrollBarVisibility='Hidden'/>
  </Border>
  <ControlTemplate.Triggers>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Chrome' Property='BorderBrush' Value='$BORDERSTRONG$'/></Trigger>
    <Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='Chrome' Property='BorderBrush' Value='$ACCENT$'/></Trigger>
    <Trigger Property='IsEnabled' Value='False'><Setter Property='Opacity' Value='0.55'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string ComboBoxTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type ComboBox}'>
  <Grid>
    <Border x:Name='Chrome' CornerRadius='5' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='{TemplateBinding BorderThickness}' SnapsToDevicePixels='True'/>
    <ToggleButton x:Name='DropDownToggle' Focusable='False' ClickMode='Press' Background='Transparent' BorderThickness='0'
                  IsChecked='{Binding IsDropDownOpen, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}'>
      <ToggleButton.Template>
        <ControlTemplate TargetType='{x:Type ToggleButton}'><Border Background='Transparent'/></ControlTemplate>
      </ToggleButton.Template>
    </ToggleButton>
    <Grid Margin='{TemplateBinding Padding}'>
      <Grid.ColumnDefinitions><ColumnDefinition Width='*'/><ColumnDefinition Width='Auto'/></Grid.ColumnDefinitions>
      <ContentPresenter x:Name='Selection' Grid.Column='0' VerticalAlignment='Center' IsHitTestVisible='False'
                        Content='{TemplateBinding SelectionBoxItem}' ContentTemplate='{TemplateBinding SelectionBoxItemTemplate}'
                        ContentStringFormat='{TemplateBinding SelectionBoxItemStringFormat}'/>
      <TextBox x:Name='PART_EditableTextBox' Grid.Column='0' Visibility='Hidden' Background='Transparent' BorderThickness='0' Padding='0'
               VerticalContentAlignment='Center' Focusable='True'>
        <TextBox.Template>
          <ControlTemplate TargetType='{x:Type TextBox}'>
            <ScrollViewer x:Name='PART_ContentHost' Focusable='False' HorizontalScrollBarVisibility='Hidden' VerticalScrollBarVisibility='Hidden'/>
          </ControlTemplate>
        </TextBox.Template>
      </TextBox>
      <Path Grid.Column='1' Data='M 0 0 L 4 4 L 8 0' Stroke='$TEXTMUTED$' StrokeThickness='1.6' VerticalAlignment='Center' Margin='4,1,4,0' IsHitTestVisible='False'/>
    </Grid>
    <Popup x:Name='PART_Popup' IsOpen='{TemplateBinding IsDropDownOpen}' Placement='Bottom' AllowsTransparency='True' Focusable='False' PopupAnimation='Fade'>
      <Border Background='$SURFACE$' BorderBrush='$BORDER$' BorderThickness='1' CornerRadius='6' Margin='0,4,0,0'
              MinWidth='{Binding ActualWidth, RelativeSource={RelativeSource TemplatedParent}}'
              MaxHeight='{TemplateBinding MaxDropDownHeight}' SnapsToDevicePixels='True'>
        <ScrollViewer Margin='4' VerticalScrollBarVisibility='Auto' HorizontalScrollBarVisibility='Disabled'>
          <ItemsPresenter KeyboardNavigation.DirectionalNavigation='Contained'/>
        </ScrollViewer>
      </Border>
    </Popup>
  </Grid>
  <ControlTemplate.Triggers>
    <Trigger Property='IsEditable' Value='True'>
      <Setter TargetName='PART_EditableTextBox' Property='Visibility' Value='Visible'/>
      <Setter TargetName='Selection' Property='Visibility' Value='Hidden'/>
    </Trigger>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Chrome' Property='BorderBrush' Value='$BORDERSTRONG$'/></Trigger>
    <Trigger Property='IsKeyboardFocusWithin' Value='True'><Setter TargetName='Chrome' Property='BorderBrush' Value='$ACCENT$'/></Trigger>
    <Trigger Property='IsEnabled' Value='False'><Setter Property='Opacity' Value='0.55'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string ComboBoxItemTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type ComboBoxItem}'>
  <Border x:Name='Bd' CornerRadius='4' Background='Transparent' Padding='{TemplateBinding Padding}' Margin='0,1'>
    <ContentPresenter TextElement.Foreground='{TemplateBinding Foreground}' VerticalAlignment='Center'/>
  </Border>
  <ControlTemplate.Triggers>
    <Trigger Property='IsHighlighted' Value='True'><Setter TargetName='Bd' Property='Background' Value='$SURFALT$'/></Trigger>
    <Trigger Property='IsSelected' Value='True'><Setter TargetName='Bd' Property='Background' Value='$ACCENTSOFT$'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string CheckBoxTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type CheckBox}'>
  <Grid Background='Transparent'>
    <Grid.ColumnDefinitions><ColumnDefinition Width='Auto'/><ColumnDefinition Width='*'/></Grid.ColumnDefinitions>
    <Border x:Name='Box' Grid.Column='0' Width='16' Height='16' CornerRadius='2' Background='$SURFACE$' BorderBrush='$BORDERSTRONG$' BorderThickness='1.5' VerticalAlignment='Center'/>
    <Path x:Name='Check' Grid.Column='0' Data='M 1 5 L 4.4 8.4 L 11 1.6' Stroke='$ONACCENT$' StrokeThickness='1.9' StrokeStartLineCap='Round' StrokeEndLineCap='Round' VerticalAlignment='Center' HorizontalAlignment='Center' Visibility='Collapsed' Margin='0,1,0,0'/>
    <ContentPresenter Grid.Column='1' Margin='8,0,0,0' VerticalAlignment='Center' RecognizesAccessKey='True' TextElement.Foreground='{TemplateBinding Foreground}' TextElement.FontSize='{TemplateBinding FontSize}'/>
  </Grid>
  <ControlTemplate.Triggers>
    <Trigger Property='IsChecked' Value='True'>
      <Setter TargetName='Check' Property='Visibility' Value='Visible'/>
      <Setter TargetName='Box' Property='Background' Value='$ACCENT$'/>
      <Setter TargetName='Box' Property='BorderBrush' Value='$ACCENT$'/>
    </Trigger>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Box' Property='BorderBrush' Value='$ACCENT$'/></Trigger>
    <Trigger Property='IsEnabled' Value='False'><Setter Property='Opacity' Value='0.5'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string ExpanderTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type Expander}'>
  <Border Background='Transparent' BorderBrush='$BORDER$' BorderThickness='0,1,0,0' SnapsToDevicePixels='True'>
    <DockPanel>
      <ToggleButton x:Name='Head' DockPanel.Dock='Top' Focusable='True' HorizontalContentAlignment='Stretch'
                    IsChecked='{Binding IsExpanded, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}'
                    Content='{TemplateBinding Header}'>
        <ToggleButton.Template>
          <ControlTemplate TargetType='{x:Type ToggleButton}'>
            <Border x:Name='Hover' CornerRadius='4' Background='Transparent' Padding='2,7,2,7'>
              <Grid>
                <Grid.ColumnDefinitions><ColumnDefinition Width='*'/><ColumnDefinition Width='Auto'/></Grid.ColumnDefinitions>
                <ContentPresenter Grid.Column='0' Content='{Binding Content, RelativeSource={RelativeSource TemplatedParent}}' VerticalAlignment='Center' TextElement.Foreground='$TEXT$'/>
                <Path x:Name='Arrow' Grid.Column='1' Data='M 0 0 L 4 4 L 8 0' Stroke='$TEXTMUTED$' StrokeThickness='1.4' VerticalAlignment='Center' Margin='8,1,0,0'>
                  <Path.RenderTransform><RotateTransform Angle='0' CenterX='4' CenterY='4'/></Path.RenderTransform>
                </Path>
              </Grid>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Hover' Property='Background' Value='$SURFALT$'/></Trigger>
              <Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='Hover' Property='Background' Value='$SURFALT$'/></Trigger>
              <Trigger Property='IsChecked' Value='True'>
                <Setter TargetName='Arrow' Property='RenderTransform'>
                  <Setter.Value><RotateTransform Angle='180' CenterX='4' CenterY='4'/></Setter.Value>
                </Setter>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </ToggleButton.Template>
      </ToggleButton>
      <ContentPresenter x:Name='Body' Margin='2,0,2,8' Visibility='Collapsed'/>
    </DockPanel>
  </Border>
  <ControlTemplate.Triggers>
    <Trigger Property='IsExpanded' Value='True'><Setter TargetName='Body' Property='Visibility' Value='Visible'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string TabItemTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type TabItem}'>
  <Border x:Name='Bd' Background='Transparent' Padding='11,7,11,6' BorderThickness='0,0,0,2' BorderBrush='Transparent'>
    <ContentPresenter ContentSource='Header' HorizontalAlignment='Center' VerticalAlignment='Center' TextElement.Foreground='{TemplateBinding Foreground}'/>
  </Border>
  <ControlTemplate.Triggers>
    <Trigger Property='IsMouseOver' Value='True'><Setter Property='Foreground' Value='$TEXT$'/></Trigger>
    <Trigger Property='IsSelected' Value='True'>
      <Setter TargetName='Bd' Property='BorderBrush' Value='$ACCENT$'/>
      <Setter Property='Foreground' Value='$TEXT$'/>
    </Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string TabControlTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type TabControl}'>
  <DockPanel Background='$PAGE$'>
    <Border DockPanel.Dock='Top' BorderBrush='$BORDER$' BorderThickness='0,0,0,1' Margin='0,0,0,8' HorizontalAlignment='Stretch'>
      <TabPanel IsItemsHost='True' HorizontalAlignment='Left'/>
    </Border>
    <ContentPresenter ContentSource='SelectedContent'/>
  </DockPanel>
</ControlTemplate>";

        private const string ChipTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type Button}'>
  <Grid>
    <Border x:Name='Chrome' CornerRadius='3' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='{TemplateBinding BorderThickness}'/>
    <Border x:Name='Shade' CornerRadius='3' Background='$OVERLAY$' Opacity='0' IsHitTestVisible='False'/>
    <ContentPresenter Margin='{TemplateBinding Padding}' HorizontalAlignment='Center' VerticalAlignment='Center' TextElement.Foreground='{TemplateBinding Foreground}'/>
  </Grid>
  <ControlTemplate.Triggers>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Shade' Property='Opacity' Value='0.05'/></Trigger>
    <Trigger Property='IsPressed' Value='True'><Setter TargetName='Shade' Property='Opacity' Value='0.1'/></Trigger>
    <Trigger Property='IsEnabled' Value='False'><Setter Property='Opacity' Value='0.4'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string ScrollBarTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type ScrollBar}'>
  <Grid Background='Transparent' Width='10'>
    <Track x:Name='PART_Track' IsDirectionReversed='True'>
      <Track.DecreaseRepeatButton><RepeatButton Command='ScrollBar.PageUpCommand' Opacity='0' Focusable='False'/></Track.DecreaseRepeatButton>
      <Track.IncreaseRepeatButton><RepeatButton Command='ScrollBar.PageDownCommand' Opacity='0' Focusable='False'/></Track.IncreaseRepeatButton>
      <Track.Thumb>
        <Thumb>
          <Thumb.Template>
            <ControlTemplate TargetType='{x:Type Thumb}'>
              <Border CornerRadius='4' Background='$SCROLL$' Margin='2,0,2,0'/>
            </ControlTemplate>
          </Thumb.Template>
        </Thumb>
      </Track.Thumb>
    </Track>
  </Grid>
</ControlTemplate>";

        private const string ScrollBarHorizontalTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type ScrollBar}'>
  <Grid Background='Transparent' Height='10'>
    <Track x:Name='PART_Track'>
      <Track.DecreaseRepeatButton><RepeatButton Command='ScrollBar.PageLeftCommand' Opacity='0' Focusable='False'/></Track.DecreaseRepeatButton>
      <Track.IncreaseRepeatButton><RepeatButton Command='ScrollBar.PageRightCommand' Opacity='0' Focusable='False'/></Track.IncreaseRepeatButton>
      <Track.Thumb>
        <Thumb>
          <Thumb.Template>
            <ControlTemplate TargetType='{x:Type Thumb}'>
              <Border CornerRadius='4' Background='$SCROLL$' Margin='0,2,0,2'/>
            </ControlTemplate>
          </Thumb.Template>
        </Thumb>
      </Track.Thumb>
    </Track>
  </Grid>
</ControlTemplate>";

        private const string ToolTipTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type ToolTip}'>
  <Border Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='{TemplateBinding BorderThickness}' CornerRadius='6' Padding='{TemplateBinding Padding}'>
    <ContentPresenter TextElement.Foreground='{TemplateBinding Foreground}' TextElement.FontSize='{TemplateBinding FontSize}'/>
  </Border>
</ControlTemplate>";

        private const string ListBoxItemTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type ListBoxItem}'>
  <Border x:Name='Bd' CornerRadius='4' Background='Transparent' Padding='{TemplateBinding Padding}' Margin='0,1'>
    <ContentPresenter TextElement.Foreground='{TemplateBinding Foreground}' TextElement.FontSize='{TemplateBinding FontSize}'/>
  </Border>
  <ControlTemplate.Triggers>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Bd' Property='Background' Value='$SURFALT$'/></Trigger>
    <Trigger Property='IsSelected' Value='True'><Setter TargetName='Bd' Property='Background' Value='$ACCENTSOFT$'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string ExpanderFlatTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type Expander}'>
  <Border Background='Transparent' BorderThickness='0' SnapsToDevicePixels='True'>
    <DockPanel>
      <ToggleButton x:Name='Head' DockPanel.Dock='Top' Focusable='True' HorizontalContentAlignment='Stretch'
                    IsChecked='{Binding IsExpanded, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}'
                    Content='{TemplateBinding Header}'>
        <ToggleButton.Template>
          <ControlTemplate TargetType='{x:Type ToggleButton}'>
            <Border x:Name='Hover' CornerRadius='5' Background='Transparent' Padding='8,6,8,6'>
              <Grid>
                <Grid.ColumnDefinitions><ColumnDefinition Width='*'/><ColumnDefinition Width='Auto'/></Grid.ColumnDefinitions>
                <ContentPresenter Grid.Column='0' Content='{Binding Content, RelativeSource={RelativeSource TemplatedParent}}' VerticalAlignment='Center' TextElement.Foreground='$TEXT$'/>
                <Path x:Name='Arrow' Grid.Column='1' Data='M 0 0 L 4 4 L 8 0' Stroke='$TEXTMUTED$' StrokeThickness='1.5' VerticalAlignment='Center' Margin='8,1,0,0'>
                  <Path.RenderTransform><RotateTransform Angle='0' CenterX='4' CenterY='4'/></Path.RenderTransform>
                </Path>
              </Grid>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Hover' Property='Background' Value='$SURFALT$'/></Trigger>
              <Trigger Property='IsChecked' Value='True'>
                <Setter TargetName='Arrow' Property='RenderTransform'>
                  <Setter.Value><RotateTransform Angle='180' CenterX='4' CenterY='4'/></Setter.Value>
                </Setter>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </ToggleButton.Template>
      </ToggleButton>
      <ContentPresenter x:Name='Body' Margin='8,0,4,6' Visibility='Collapsed'/>
    </DockPanel>
  </Border>
  <ControlTemplate.Triggers>
    <Trigger Property='IsExpanded' Value='True'><Setter TargetName='Body' Property='Visibility' Value='Visible'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string ListViewItemTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type ListViewItem}'>
  <Border x:Name='Bd' Background='Transparent' Padding='2,0' BorderThickness='0'>
    <GridViewRowPresenter Content='{TemplateBinding Content}' Columns='{TemplateBinding GridView.ColumnCollection}' VerticalAlignment='Center'/>
  </Border>
  <ControlTemplate.Triggers>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Bd' Property='Background' Value='$SURFALT$'/></Trigger>
    <Trigger Property='IsSelected' Value='True'><Setter TargetName='Bd' Property='Background' Value='$ACCENTSOFT$'/></Trigger>
  </ControlTemplate.Triggers>
</ControlTemplate>";

        private const string GridViewColumnHeaderTemplateXaml = @"<ControlTemplate " + Head + @" TargetType='{x:Type GridViewColumnHeader}'>
  <Border Background='$SUNKEN$' BorderBrush='$BORDER$' BorderThickness='0,0,0,1' Padding='8,5'>
    <ContentPresenter VerticalAlignment='Center' TextElement.Foreground='$TEXT2$' TextElement.FontSize='11' TextElement.FontWeight='SemiBold'/>
  </Border>
</ControlTemplate>";

        // ---------- 样式 ----------

        private static void Set(Style style, DependencyProperty property, object value)
            => style.Setters.Add(new Setter(property, value));

        private static Style NewStyle(Type target, Style basedOn = null)
        {
            var style = new Style(target);
            if (basedOn != null) style.BasedOn = basedOn;
            return style;
        }

        private static void BuildStyles()
        {
            TextBlockImplicit = MakeTextBlockStyle();
            ButtonImplicit = MakeButtonStyle();
            TextBoxImplicit = MakeTextBoxStyle();
            PasswordImplicit = MakePasswordStyle();
            ComboImplicit = MakeComboStyle();
            ComboItemImplicit = MakeComboItemStyle();
            CheckImplicit = MakeCheckStyle();
            ExpanderImplicit = MakeExpanderStyle();
            ExpanderFlatStyle = MakeExpanderFlatStyle();
            ListBoxItemImplicit = MakeListBoxItemStyle();
            ListViewImplicit = MakeListViewStyle();
            ListViewItemImplicit = MakeListViewItemStyle();
            GridViewHeaderImplicit = MakeGridViewHeaderStyle();
            ScrollBarImplicit = MakeScrollBarStyle();
            ToolTipImplicit = MakeToolTipStyle();
            TabControlImplicit = MakeTabControlStyle();
            TabItemImplicit = MakeTabItemStyle();
            AccentButtonStyle = MakeAccentStyle();
            QuietButtonStyle = MakeQuietStyle();
            DangerButtonStyle = MakeDangerStyle();
            ChipButtonStyle = MakeChipStyle();
            SectionTitleStyle = MakeSectionTitleStyle();
            CaptionStyle = MakeCaptionStyle();
            MonoStyle = MakeMonoStyle();
            SmallStyle = MakeSmallStyle();

            _dictionary = new ResourceDictionary
            {
                [typeof(TextBlock)] = TextBlockImplicit,
                [typeof(Button)] = ButtonImplicit,
                [typeof(TextBox)] = TextBoxImplicit,
                [typeof(PasswordBox)] = PasswordImplicit,
                [typeof(ComboBox)] = ComboImplicit,
                [typeof(ComboBoxItem)] = ComboItemImplicit,
                [typeof(CheckBox)] = CheckImplicit,
                [typeof(Expander)] = ExpanderImplicit,
                [typeof(ListBoxItem)] = ListBoxItemImplicit,
                [typeof(ListView)] = ListViewImplicit,
                [typeof(ListViewItem)] = ListViewItemImplicit,
                [typeof(GridViewColumnHeader)] = GridViewHeaderImplicit,
                [typeof(ScrollBar)] = ScrollBarImplicit,
                [typeof(ToolTip)] = ToolTipImplicit,
                [typeof(TabControl)] = TabControlImplicit,
                [typeof(TabItem)] = TabItemImplicit,
                ["Ui.AccentButton"] = AccentButtonStyle,
                ["Ui.QuietButton"] = QuietButtonStyle,
                ["Ui.DangerButton"] = DangerButtonStyle,
                ["Ui.ChipButton"] = ChipButtonStyle
            };
        }

        private static Style MakeTextBlockStyle()
        {
            var style = NewStyle(typeof(TextBlock));
            Set(style, TextBlock.FontFamilyProperty, FontFamily);
            Set(style, TextBlock.FontSizeProperty, 11.5);
            Set(style, TextBlock.ForegroundProperty, Brush(TextPrimary));
            return style;
        }

        private static Style MakeButtonStyle()
        {
            var style = NewStyle(typeof(Button));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.5);
            Set(style, Control.FontWeightProperty, FontWeights.SemiBold);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.BackgroundProperty, Brush(Surface));
            Set(style, Control.BorderBrushProperty, Brush(Border));
            Set(style, Control.BorderThicknessProperty, new Thickness(1));
            Set(style, Control.PaddingProperty, new Thickness(10, 4, 10, 4));
            Set(style, Control.MinHeightProperty, 28.0);
            Set(style, Control.CursorProperty, System.Windows.Input.Cursors.Hand);
            Set(style, Control.HorizontalContentAlignmentProperty, HorizontalAlignment.Center);
            Set(style, Control.VerticalContentAlignmentProperty, VerticalAlignment.Center);
            Set(style, FrameworkElement.SnapsToDevicePixelsProperty, true);
            if (_buttonTemplate != null) Set(style, Control.TemplateProperty, _buttonTemplate);
            return style;
        }

        private static Style MakeAccentStyle()
        {
            var style = NewStyle(typeof(Button), ButtonImplicit ?? MakeButtonStyle());
            Set(style, Control.BackgroundProperty, Brush(Fill));
            Set(style, Control.BorderBrushProperty, Brush(Fill));
            Set(style, Control.ForegroundProperty, Brush(OnFill));
            Set(style, Control.FontWeightProperty, FontWeights.Bold);
            Set(style, Control.MinHeightProperty, 30.0);
            Set(style, Control.PaddingProperty, new Thickness(12, 6, 12, 6));
            return style;
        }

        private static Style MakeQuietStyle()
        {
            var style = NewStyle(typeof(Button), ButtonImplicit ?? MakeButtonStyle());
            Set(style, Control.BackgroundProperty, Brushes.Transparent);
            Set(style, Control.BorderThicknessProperty, new Thickness(0));
            Set(style, Control.ForegroundProperty, Brush(TextSecondary));
            Set(style, Control.FontWeightProperty, FontWeights.Normal);
            Set(style, Control.MinHeightProperty, 24.0);
            Set(style, Control.PaddingProperty, new Thickness(8, 3, 8, 3));
            return style;
        }

        private static Style MakeDangerStyle()
        {
            var style = NewStyle(typeof(Button), ButtonImplicit ?? MakeButtonStyle());
            Set(style, Control.BackgroundProperty, Brush(Surface));
            Set(style, Control.BorderBrushProperty, Brush(Danger));
            Set(style, Control.ForegroundProperty, Brush(Danger));
            return style;
        }

        private static Style MakeChipStyle()
        {
            var style = NewStyle(typeof(Button), ButtonImplicit ?? MakeButtonStyle());
            Set(style, Control.BackgroundProperty, Brush(SurfaceAlt));
            Set(style, Control.BorderBrushProperty, Brush(Border));
            Set(style, Control.ForegroundProperty, Brush(TextSecondary));
            Set(style, Control.FontWeightProperty, FontWeights.Normal);
            Set(style, Control.FontSizeProperty, 11.0);
            Set(style, Control.MinHeightProperty, 24.0);
            Set(style, Control.PaddingProperty, new Thickness(10, 2, 10, 2));
            if (_chipTemplate != null) Set(style, Control.TemplateProperty, _chipTemplate);
            return style;
        }

        private static Style MakeTextBoxStyle()
        {
            var style = NewStyle(typeof(TextBox));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.5);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.BackgroundProperty, Brush(Surface));
            Set(style, Control.BorderBrushProperty, Brush(Border));
            Set(style, Control.BorderThicknessProperty, new Thickness(1));
            Set(style, Control.PaddingProperty, new Thickness(7, 4, 7, 4));
            Set(style, Control.MinHeightProperty, 28.0);
            Set(style, Control.VerticalContentAlignmentProperty, VerticalAlignment.Center);
            Set(style, TextBoxBase.SelectionBrushProperty, Brush(AccentSoft));
            Set(style, TextBoxBase.CaretBrushProperty, Brush(Accent));
            if (_textBoxTemplate != null) Set(style, Control.TemplateProperty, _textBoxTemplate);
            return style;
        }

        private static Style MakePasswordStyle()
        {
            var style = NewStyle(typeof(PasswordBox));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.5);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.BackgroundProperty, Brush(Surface));
            Set(style, Control.BorderBrushProperty, Brush(Border));
            Set(style, Control.BorderThicknessProperty, new Thickness(1));
            Set(style, Control.PaddingProperty, new Thickness(7, 4, 7, 4));
            Set(style, Control.MinHeightProperty, 28.0);
            Set(style, Control.VerticalContentAlignmentProperty, VerticalAlignment.Center);
            if (_passwordTemplate != null) Set(style, Control.TemplateProperty, _passwordTemplate);
            return style;
        }

        private static Style MakeComboStyle()
        {
            var style = NewStyle(typeof(ComboBox));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.5);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.BackgroundProperty, Brush(Surface));
            Set(style, Control.BorderBrushProperty, Brush(Border));
            Set(style, Control.BorderThicknessProperty, new Thickness(1));
            Set(style, Control.PaddingProperty, new Thickness(8, 3, 4, 3));
            Set(style, Control.MinHeightProperty, 28.0);
            Set(style, Control.VerticalContentAlignmentProperty, VerticalAlignment.Center);
            Set(style, FrameworkElement.SnapsToDevicePixelsProperty, true);
            if (_comboTemplate != null) Set(style, Control.TemplateProperty, _comboTemplate);
            return style;
        }

        private static Style MakeComboItemStyle()
        {
            var style = NewStyle(typeof(ComboBoxItem));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.5);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.PaddingProperty, new Thickness(9, 6, 9, 6));
            Set(style, Control.HorizontalContentAlignmentProperty, HorizontalAlignment.Left);
            if (_comboItemTemplate != null) Set(style, Control.TemplateProperty, _comboItemTemplate);
            return style;
        }

        private static Style MakeCheckStyle()
        {
            var style = NewStyle(typeof(CheckBox));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.5);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.CursorProperty, System.Windows.Input.Cursors.Hand);
            Set(style, Control.VerticalContentAlignmentProperty, VerticalAlignment.Center);
            if (_checkTemplate != null) Set(style, Control.TemplateProperty, _checkTemplate);
            return style;
        }

        private static Style MakeExpanderStyle()
        {
            var style = NewStyle(typeof(Expander));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 12.0);
            Set(style, Control.FontWeightProperty, FontWeights.SemiBold);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.MarginProperty, new Thickness(0, 0, 0, 6));
            Set(style, FrameworkElement.SnapsToDevicePixelsProperty, true);
            if (_expanderTemplate != null) Set(style, Control.TemplateProperty, _expanderTemplate);
            return style;
        }

        private static Style MakeExpanderFlatStyle()
        {
            var style = NewStyle(typeof(Expander), ExpanderImplicit ?? MakeExpanderStyle());
            Set(style, Control.FontWeightProperty, FontWeights.Normal);
            Set(style, Control.MarginProperty, new Thickness(0, 0, 0, 2));
            if (_expanderFlatTemplate != null) Set(style, Control.TemplateProperty, _expanderFlatTemplate);
            return style;
        }

        private static Style MakeListBoxItemStyle()
        {
            var style = NewStyle(typeof(ListBoxItem));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.5);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.PaddingProperty, new Thickness(8, 4, 8, 4));
            Set(style, Control.HorizontalContentAlignmentProperty, HorizontalAlignment.Left);
            if (_listBoxItemTemplate != null) Set(style, Control.TemplateProperty, _listBoxItemTemplate);
            return style;
        }

        private static Style MakeListViewStyle()
        {
            var style = NewStyle(typeof(ListView));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.5);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.BackgroundProperty, Brush(Surface));
            Set(style, Control.BorderBrushProperty, Brush(Border));
            Set(style, Control.BorderThicknessProperty, new Thickness(1));
            Set(style, Control.PaddingProperty, new Thickness(0));
            return style;
        }

        private static Style MakeListViewItemStyle()
        {
            var style = NewStyle(typeof(ListViewItem));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.5);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.PaddingProperty, new Thickness(6, 3, 6, 3));
            Set(style, Control.HorizontalContentAlignmentProperty, HorizontalAlignment.Stretch);
            if (_listViewItemTemplate != null) Set(style, Control.TemplateProperty, _listViewItemTemplate);
            return style;
        }

        private static Style MakeGridViewHeaderStyle()
        {
            var style = NewStyle(typeof(GridViewColumnHeader));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.0);
            Set(style, Control.ForegroundProperty, Brush(TextSecondary));
            Set(style, Control.BackgroundProperty, Brush(Sunken));
            if (_gridHeaderTemplate != null) Set(style, Control.TemplateProperty, _gridHeaderTemplate);
            return style;
        }

        private static Style MakeScrollBarStyle()
        {
            var style = NewStyle(typeof(ScrollBar));
            Set(style, FrameworkElement.WidthProperty, 10.0);
            Set(style, Control.BackgroundProperty, Brushes.Transparent);
            if (_scrollTemplate != null) Set(style, Control.TemplateProperty, _scrollTemplate);
            var horizontal = new Trigger { Property = ScrollBar.OrientationProperty, Value = Orientation.Horizontal };
            horizontal.Setters.Add(new Setter(FrameworkElement.WidthProperty, double.NaN));
            horizontal.Setters.Add(new Setter(FrameworkElement.HeightProperty, 10.0));
            if (_scrollHoriTemplate != null) horizontal.Setters.Add(new Setter(Control.TemplateProperty, _scrollHoriTemplate));
            style.Triggers.Add(horizontal);
            return style;
        }

        private static Style MakeToolTipStyle()
        {
            var style = NewStyle(typeof(ToolTip));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 11.0);
            Set(style, Control.ForegroundProperty, Brush(TextPrimary));
            Set(style, Control.BackgroundProperty, Brush(Surface));
            Set(style, Control.BorderBrushProperty, Brush(BorderStrong));
            Set(style, Control.BorderThicknessProperty, new Thickness(1));
            Set(style, Control.PaddingProperty, new Thickness(9, 6, 9, 6));
            if (_toolTipTemplate != null) Set(style, Control.TemplateProperty, _toolTipTemplate);
            return style;
        }

        private static Style MakeTabItemStyle()
        {
            var style = NewStyle(typeof(TabItem));
            Set(style, Control.FontFamilyProperty, FontFamily);
            Set(style, Control.FontSizeProperty, 12.0);
            Set(style, Control.FontWeightProperty, FontWeights.SemiBold);
            Set(style, Control.ForegroundProperty, Brush(TextSecondary));
            Set(style, Control.PaddingProperty, new Thickness(6, 0, 6, 0));
            Set(style, Control.BackgroundProperty, Brushes.Transparent);
            if (_tabItemTemplate != null) Set(style, Control.TemplateProperty, _tabItemTemplate);
            return style;
        }

        private static Style MakeTabControlStyle()
        {
            var style = NewStyle(typeof(TabControl));
            Set(style, Control.BackgroundProperty, Brushes.Transparent);
            Set(style, Control.BorderThicknessProperty, new Thickness(0));
            if (_tabControlTemplate != null) Set(style, Control.TemplateProperty, _tabControlTemplate);
            return style;
        }

        private static Style MakeSectionTitleStyle()
        {
            var style = NewStyle(typeof(TextBlock));
            Set(style, TextBlock.FontFamilyProperty, FontFamily);
            Set(style, TextBlock.FontSizeProperty, 12.0);
            Set(style, TextBlock.FontWeightProperty, FontWeights.SemiBold);
            Set(style, TextBlock.ForegroundProperty, Brush(TextPrimary));
            Set(style, TextBlock.MarginProperty, new Thickness(0, 4, 0, 4));
            return style;
        }

        private static Style MakeCaptionStyle()
        {
            var style = NewStyle(typeof(TextBlock), TextBlockImplicit ?? MakeTextBlockStyle());
            Set(style, TextBlock.FontSizeProperty, 10.5);
            Set(style, TextBlock.ForegroundProperty, Brush(TextSecondary));
            Set(style, TextBlock.TextWrappingProperty, TextWrapping.Wrap);
            Set(style, TextBlock.LineHeightProperty, 16.0);
            return style;
        }
        private static Style MakeMonoStyle()
        {
            var style = NewStyle(typeof(TextBlock));
            Set(style, TextBlock.FontFamilyProperty, new FontFamily("Cascadia Mono, Consolas"));
            Set(style, TextBlock.FontSizeProperty, 10.5);
            Set(style, TextBlock.ForegroundProperty, Brush(TextSecondary));
            return style;
        }

        private static Style MakeSmallStyle()
        {
            var style = NewStyle(typeof(TextBlock), TextBlockImplicit ?? MakeTextBlockStyle());
            Set(style, TextBlock.FontSizeProperty, 10.0);
            Set(style, TextBlock.ForegroundProperty, Brush(TextMuted));
            return style;
        }

        // ---------- 对外样式入口 ----------

        internal static void Primary(Button button) => Apply(button, AccentButtonStyle);
        internal static void Quiet(Button button) => Apply(button, QuietButtonStyle);
        internal static void Chip(Button button) => Apply(button, ChipButtonStyle);
        internal static void DangerApply(Button button) => Apply(button, DangerButtonStyle);

        /// <summary>状态文案分级：失败红、待办橙、成功绿、其它中性。判断从严，忙态与普通提示保持中性。</summary>
        internal static Tone ToneOf(string text)
        {
            var value = text ?? "";
            if (Has(value, "失败", "错误", "异常", "无法", "未保存", "未生成", "未通过", "缺少", "不能", "拒绝", "超时", "无效", "不可用"))
                return Tone.Danger;
            if (Has(value, "请先", "请选择", "请填写", "请检查", "需要先", "注意", "待人工", "未填写", "未选择"))
                return Tone.Warning;
            if (Has(value, "停止", "取消", "跳过")) return Tone.Neutral;
            if (Has(value, "已完成", "完成：", "保存成功", "已保存", "已生成", "已确认", "已导出", "已加载", "已通过", "成功", "通过"))
                return Tone.Success;
            return Tone.Neutral;
        }

        /// <summary>按状态文案语义着色，避免成功/失败共用一种颜色。文案可继续由现有逻辑写入。</summary>
        internal static void ToneStatus(System.Windows.Controls.TextBlock block, string text)
        {
            if (block == null) return;
            switch (ToneOf(text))
            {
                case Tone.Danger: block.Foreground = Brush(Danger); break;
                case Tone.Warning: block.Foreground = Brush(Warning); break;
                case Tone.Success: block.Foreground = Brush(Success); break;
                default: block.Foreground = Brush(TextSecondary); break;
            }
        }

        /// <summary>监听状态文本变化并按语义着色（TextBlock 没有 TextChanged 事件）。</summary>
        internal static void WatchStatus(System.Windows.Controls.TextBlock block)
        {
            if (block == null) return;
            try
            {
                System.ComponentModel.DependencyPropertyDescriptor
                    .FromProperty(System.Windows.Controls.TextBlock.TextProperty, typeof(System.Windows.Controls.TextBlock))
                    .AddValueChanged(block, (_, __) => ToneStatus(block, block.Text));
                ToneStatus(block, block.Text);
            }
            catch (Exception ex) { System.Diagnostics.Trace.WriteLine("状态着色监听失败：" + ex.Message); }
        }

        /// <summary>把嵌套在另一层 Expander 内的折叠区改为无边框样式，避免"盒中盒"层层吃留白。</summary>
        internal static void FlattenNestedExpanders(DependencyObject root)
        {
            if (root == null || ExpanderFlatStyle == null) return;
            try { Walk(root, 0); }
            catch (Exception ex) { System.Diagnostics.Trace.WriteLine("折叠区扁平化失败：" + ex.Message); }

            void Walk(DependencyObject node, int depth)
            {
                if (node == null) return;
                if (node is Expander expander && depth > 0) expander.Style = ExpanderFlatStyle;
                var next = node is Expander ? depth + 1 : depth;
                foreach (var child in LogicalTreeHelper.GetChildren(node))
                    if (child is DependencyObject childNode) Walk(childNode, next);
            }
        }

        private static bool Has(string value, params string[] keys)
        {
            foreach (var key in keys) if (value.Contains(key)) return true;
            return false;
        }

        private static void Apply(Button button, Style style)
        {
            if (button != null && style != null) button.Style = style;
        }
    }

    /// <summary>间距刻度：新代码优先使用；旧面板的内联 Thickness 仍待分批收敛。</summary>
    internal static class Space
    {
        internal const double Xs = 4, Sm = 8, Md = 12, Lg = 16, Xl = 24;

        internal static Thickness All(double value) => new Thickness(value);
        internal static Thickness Symmetric(double horizontal, double vertical) => new Thickness(horizontal, vertical, horizontal, vertical);
        internal static Thickness Bottom(double value) => new Thickness(0, 0, 0, value);
        internal static Thickness Top(double value) => new Thickness(0, value, 0, 0);
    }
}