using System;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Text;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Mapping;
using ArcGIS.Desktop.Mapping.Events;

namespace ProAiBridge
{
    internal static class Bridge
    {
        internal static string PipeName => "pro-ai-bridge-v1-" + System.Diagnostics.Process.GetCurrentProcess().Id;
        internal const int MaxBytes = 1_048_576;
        private static readonly CancellationTokenSource StopToken = new CancellationTokenSource();
        private static readonly Action<MapClosedEventArgs> MapClosedHandler = OnMapClosed;
        private static int _started;
        internal static volatile bool IsListening;
        internal static void Start()
        {
            if (Interlocked.Exchange(ref _started, 1) != 0) return;
            // 目录坏了只让桥接不可用，不要让整个 add-in 初始化失败；失败可重试
            try
            {
                ToolProtocol.ValidateCatalog();
            }
            catch (Exception ex)
            {
                LastCatalogError = ex.Message;
                System.Diagnostics.Trace.WriteLine("Pro AI Bridge 工具目录校验失败：" + ex.Message);
                Interlocked.Exchange(ref _started, 0);
                return;
            }
            LastCatalogError = "";
            MapClosedEvent.Subscribe(MapClosedHandler, true);
            _ = Task.Run(ListenAsync);
        }

        /// <summary>工具目录校验失败原因；空字符串表示通过。</summary>
        internal static volatile string LastCatalogError = "";
        internal static void Stop()
        {
            MapClosedEvent.Unsubscribe(MapClosedHandler);
            StopToken.Cancel(); BasemapPreview.CloseActive(); BasemapPreview.CloseAllMaps();
        }

        private static async void OnMapClosed(MapClosedEventArgs args)
        {
            var map = args?.MapPane?.MapView?.Map;
            if (map == null) return;
            try
            {
                var anotherViewIsOpen = await MapActions.Ui(() => FrameworkApplication.Panes.OfType<IMapPane>()
                    .Any(pane => !ReferenceEquals(pane, args.MapPane) && pane.MapView?.Map?.URI == map.URI));
                if (!anotherViewIsOpen) MapActions.CloseMap(map);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Trace.WriteLine("关闭地图底图代理检查失败：" + ex.Message);
            }
        }
        private static async Task ListenAsync()
        {
            while (!StopToken.IsCancellationRequested)
            {
                NamedPipeServerStream pipe = null;
                try
                {
                    pipe = new NamedPipeServerStream(PipeName, PipeDirection.InOut, 4,
                        PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly, 65536, 65536);
                    IsListening = true;
                    await pipe.WaitForConnectionAsync(StopToken.Token);
                    var connected = pipe; pipe = null;
                    _ = Task.Run(() => HandleAsync(connected));
                }
                catch (OperationCanceledException) { break; }
                catch (Exception ex) { System.Diagnostics.Trace.WriteLine("Pro AI Bridge: " + ex.Message); await Task.Delay(200); }
                finally { pipe?.Dispose(); }
            }
            IsListening = false;
        }

        private static async Task HandleAsync(NamedPipeServerStream pipe)
        {
            using (pipe)
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(StopToken.Token))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(80));
                try
                {
                    JsonObject response;
                    try
                    {
                        using var reader = new StreamReader(pipe, new UTF8Encoding(false, true), false, 8192, true);
                        var text = new StringBuilder(); var one = new char[1];
                        while (text.Length <= 65536)
                        {
                            var n = await reader.ReadAsync(one.AsMemory(), deadline.Token);
                            if (n == 0) throw new IOException("请求未完成即断开。");
                            if (one[0] == '\n') break;
                            text.Append(one[0]);
                        }
                        if (text.Length > 65536) throw new ArgumentException("请求超过64K字符。");
                        var request = JsonNode.Parse(text.ToString()).AsObject();
                        var name = (string)request["name"] ?? "";
                        var args = request["arguments"]?.AsObject() ?? new JsonObject();
                        var result = await MapActions.ExecuteAsync(name, args, deadline.Token, (string)request["expected_project"] ?? "", (string)request["expected_map"] ?? "");
                        response = new JsonObject { ["ok"] = true, ["result"] = result };
                    }
                    catch (OperationCanceledException) { response = new JsonObject { ["ok"] = false, ["error"] = "操作已取消或响应超时；请核对当前地图与输出，已经完成的写入不会回滚。" }; }
                    catch (Exception ex) { response = new JsonObject { ["ok"] = false, ["error"] = AcceptanceAddin.WorkbenchDiagnostics.Redact(ex.Message),
                        ["diagnostic"] = AcceptanceAddin.WorkbenchDiagnostics.Error(ex, "bridge") }; }
                    var bytes = Encoding.UTF8.GetBytes(response.ToJsonString() + "\n");
                    if (bytes.Length > MaxBytes) bytes = Encoding.UTF8.GetBytes("{\"ok\":false,\"error\":\"响应超过1MB，请缩小查询。\"}\n");
                    // A timed-out operation still gets a short response budget.
                    using var send = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                    await pipe.WriteAsync(bytes.AsMemory(), send.Token);
                    await pipe.FlushAsync(send.Token);
                }
                catch (Exception ex) { System.Diagnostics.Trace.WriteLine("Pro AI Bridge connection: " + ex.Message); }
            }
        }
    }
}
