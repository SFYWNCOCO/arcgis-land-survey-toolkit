using System;
using System.IO;
using System.Reflection;
using System.Windows;

namespace AcceptanceAddin
{
    internal static class ToolboxLocator
    {
        internal static string GetToolPath(string toolName, string toolboxFile = "SupplementaryCultivatedLandAcceptance.pyt")
        {
            var assemblyDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            var candidates = new[]
            {
                Path.GetFullPath(Path.Combine(assemblyDir, "..", "toolboxes", toolboxFile)),
                Path.Combine(assemblyDir, "toolboxes", toolboxFile)
            };
            foreach (var toolbox in candidates)
                if (File.Exists(toolbox)) return toolbox + "\\" + toolName;
            throw new FileNotFoundException("插件自带 Python 工具箱未找到。请重新安装 SupplementaryCultivatedLandAcceptance.esriAddInX。检查路径：" + String.Join("; ", candidates));
        }

        internal static void Open(string toolName, string toolboxFile = "SupplementaryCultivatedLandAcceptance.pyt")
        {
            try
            {
                if (WorkbenchJobService.IsBusy) throw new InvalidOperationException("工作台已有任务运行，请等待或停止后再打开新的处理工具。");
                ArcGIS.Desktop.Core.Geoprocessing.Geoprocessing.OpenToolDialog(GetToolPath(toolName, toolboxFile), null);
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "补充耕地验收助手", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }
    }
}
