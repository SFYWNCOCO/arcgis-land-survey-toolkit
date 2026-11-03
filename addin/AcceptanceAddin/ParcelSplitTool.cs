using System;
using System.Threading.Tasks;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Mapping;

namespace AcceptanceAddin
{
    internal sealed class ParcelSplitTool : MapTool
    {
        internal static Func<Polyline, Task> Submit;
        internal static Action Cancelled;
        public ParcelSplitTool()
        {
            IsSketchTool = true;
            SketchType = SketchGeometryType.Line;
            SketchOutputMode = SketchOutputMode.Map;
        }

        protected override async Task<bool> OnSketchCompleteAsync(Geometry geometry)
        {
            var callback = Submit;
            Submit = null;
            if (!(geometry is Polyline line) || callback == null) return false;
            try { await callback(line); return true; }
            finally { await FrameworkApplication.SetCurrentToolAsync("esri_mapping_exploreTool"); }
        }

        protected override Task OnToolDeactivateAsync(bool hasMapViewChanged)
        {
            Submit = null;
            var callback = Cancelled; Cancelled = null; callback?.Invoke();
            return Task.CompletedTask;
        }
    }
}
