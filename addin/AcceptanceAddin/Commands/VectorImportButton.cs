using ArcGIS.Desktop.Framework.Contracts;
namespace AcceptanceAddin
{
    internal class VectorImportButton : Button { protected override void OnClick() => ToolboxLocator.Open("ImportVector"); }
    internal class VectorBatchImportButton : Button { protected override void OnClick() => ToolboxLocator.Open("BatchImportVector"); }
}
