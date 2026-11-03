using ArcGIS.Desktop.Framework.Contracts;
namespace AcceptanceAddin { internal class RTKImportButton : Button { protected override void OnClick() => ToolboxLocator.Open("ImportRTK"); } }
