using ArcGIS.Desktop.Framework.Contracts;
namespace AcceptanceAddin { internal class DataIntakeButton : Button { protected override void OnClick() => ToolboxLocator.Open("ImportCAD"); } }
