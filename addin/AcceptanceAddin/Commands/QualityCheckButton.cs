using ArcGIS.Desktop.Framework.Contracts;
namespace AcceptanceAddin { internal class QualityCheckButton : Button { protected override void OnClick() => ToolboxLocator.Open("QualityCheck"); } }
