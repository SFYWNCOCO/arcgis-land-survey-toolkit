using ArcGIS.Desktop.Framework.Contracts;
namespace AcceptanceAddin { internal class RemoteSensingButton : Button { protected override void OnClick() => ToolboxLocator.Open("Sentinel2CatalogSearch"); } }
