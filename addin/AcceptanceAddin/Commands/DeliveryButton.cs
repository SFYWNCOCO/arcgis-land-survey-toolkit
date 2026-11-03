using ArcGIS.Desktop.Framework.Contracts;
namespace AcceptanceAddin { internal class DeliveryButton : Button { protected override void OnClick() => ToolboxLocator.Open("DeliveryCheck"); } }
