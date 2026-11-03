using ArcGIS.Desktop.Framework.Contracts;
namespace AcceptanceAddin { internal class ProjectSetupButton : Button { protected override void OnClick() => ToolboxLocator.Open("CreateProject"); } }
