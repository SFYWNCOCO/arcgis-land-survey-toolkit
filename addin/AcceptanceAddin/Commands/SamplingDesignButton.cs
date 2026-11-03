using ArcGIS.Desktop.Framework.Contracts;
namespace AcceptanceAddin { internal class SamplingDesignButton : Button { protected override void OnClick() => ToolboxLocator.Open("CompositeSampling"); } }
