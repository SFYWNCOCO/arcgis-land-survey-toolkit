using ArcGIS.Desktop.Framework.Contracts;
namespace AcceptanceAddin
{
    internal class ParcelLocateButton : Button { protected override void OnClick() => ParcelLocatorWindow.Open(); }
    internal class ParcelAuditButton : Button { protected override void OnClick() => ToolboxLocator.Open("ParcelAudit", "ParcelTools.pyt"); }
    internal class ParcelPrepareButton : Button { protected override void OnClick() => ToolboxLocator.Open("ParcelPrepare", "ParcelTools.pyt"); }
    internal class ParcelMapsButton : Button { protected override void OnClick() => ToolboxLocator.Open("ParcelMaps", "ParcelTools.pyt"); }
    internal class AmapPoiCredentialButton : Button { protected override void OnClick() => AmapPoiCredentialWindow.Open(); }
    internal class AmapPoiSearchButton : Button { protected override void OnClick() => ToolboxLocator.Open("POIReferenceSearch", "ParcelTools.pyt"); }
}
