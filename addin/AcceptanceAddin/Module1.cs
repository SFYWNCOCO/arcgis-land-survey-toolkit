using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;

namespace AcceptanceAddin
{
    internal class Module1 : Module
    {
        private static Module1 _this;
        public static Module1 Current => _this ?? (_this = (Module1)FrameworkApplication.FindModule("AcceptanceAddin_Module"));
        protected override bool Initialize()
        {
            var initialized = base.Initialize();
            if (initialized)
            {
                // 上次会话登记待清理的临时绘制 GDB 在启动时删除（此时无图层引用，递归删除安全）。
                try { ProAiBridge.MapActions.CleanupPendingDrawings(); }
                catch (System.Exception) { }
                LiveMcpBridge.Start(); ProAiBridge.Bridge.Start();
            }
            return initialized;
        }
        protected override void Uninitialize()
        {
            ProAiBridge.Bridge.Stop(); LiveMcpBridge.Stop(); base.Uninitialize();
        }
    }
}
