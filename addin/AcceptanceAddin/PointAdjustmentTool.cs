using System;
using System.Threading.Tasks;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Mapping;

namespace AcceptanceAddin
{
    internal sealed class PointAdjustmentTool : MapTool
    {
        internal static string TaskRoot;
        internal static string PointId;
        internal static string Operator;
        internal static string ExpectedSignature;
        internal static int ExpectedRevision;
        private bool _editing;
        public PointAdjustmentTool()
        {
            IsSketchTool = true;
            SketchType = SketchGeometryType.Point;
            SketchOutputMode = SketchOutputMode.Map;
        }

        protected override async Task<bool> OnSketchCompleteAsync(Geometry geometry)
        {
            if (_editing) return false;
            var taskRoot = TaskRoot;
            var pointId = PointId;
            var operatorName = Operator;
            var signature = ExpectedSignature;
            var revision = ExpectedRevision;
            if (!(geometry is MapPoint point) || string.IsNullOrWhiteSpace(TaskRoot) ||
                string.IsNullOrWhiteSpace(PointId) || point.SpatialReference?.Wkid <= 0) return false;
            _editing = true;
            WorkbenchJobService.JobLease job = null;
            try
            {
                job = WorkbenchJobService.Begin("调整设计点", taskRoot); job.Running();
                var tool = ToolboxLocator.GetToolPath("AdjustDesignPoint", "GridSampling.pyt");
                var result = await Geoprocessing.ExecuteToolAsync(tool, Geoprocessing.MakeValueArray(
                    taskRoot, pointId, point.X, point.Y, point.SpatialReference.Wkid, operatorName, signature, revision),
                    null, System.Threading.CancellationToken.None, null, GPExecuteToolFlags.GPThread);
                if (result == null || result.IsFailed)
                    throw new InvalidOperationException("点位未调整，请查看地理处理日志；原点位保持或按收据恢复。");
                var pane = AcceptanceWorkspacePane.ExistingSampling;
                job.Finish("Succeeded"); job.Dispose();
                if (pane != null) await pane.ReloadAfterAdjustment(taskRoot);
                await FrameworkApplication.SetCurrentToolAsync("esri_mapping_exploreTool");
                return true;
            }
            catch (Exception ex)
            {
                job?.Finish("Failed");
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show(ex.Message, "设计点调整");
                return false;
            }
            finally { job?.Dispose(); _editing = false; }
        }
    }
}
