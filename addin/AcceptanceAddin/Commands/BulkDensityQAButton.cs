using ArcGIS.Desktop.Framework.Contracts;

namespace AcceptanceAddin
{
    internal class BulkDensityQAButton : Button
    {
        protected override void OnClick() => ToolboxLocator.Open("BulkDensitySamplingQA");
    }
}
