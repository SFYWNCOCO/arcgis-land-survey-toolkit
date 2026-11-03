using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;

namespace AcceptanceAddin
{
    internal class WizardPaneButton : Button
    {
        protected override void OnClick()
        {
            AcceptanceWorkspacePane.Show("assistant");
        }
    }

    internal class SamplingWorkspaceButton : Button
    {
        protected override void OnClick() => AcceptanceWorkspacePane.Show("sampling");
    }

    internal class DemTerrainButton : Button
    {
        protected override void OnClick() => ToolboxLocator.Open("ExtractDemTerrain", "GridSampling.pyt");
    }

    internal class SamplingCoordinatesButton : Button
    {
        protected override void OnClick() => ToolboxLocator.Open("ExportSamplingCoordinates", "GridSampling.pyt");
    }

    internal class ImageryAccuracyButton : Button
    {
        protected override void OnClick() => ToolboxLocator.Open("ImageryAccuracyCheck", "SupplementaryCultivatedLandAcceptance.pyt");
    }

}
