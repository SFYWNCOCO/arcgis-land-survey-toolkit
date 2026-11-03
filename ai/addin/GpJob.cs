using System;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;

namespace ProAiBridge
{
    // The single task record used by the SDK worker, pane, MCP and core tests.
    internal sealed class GpJob : IDisposable
    {
        internal readonly string Id = Guid.NewGuid().ToString("N");
        internal readonly DateTimeOffset Created = DateTimeOffset.UtcNow;
        internal readonly string Project;
        internal readonly CancellationToken Token;
        private readonly CancellationTokenSource _cancellation = new CancellationTokenSource();
        private readonly Stopwatch _elapsed = new Stopwatch();
        private readonly object _lock = new object();
        private readonly string _tool;
        private string _output;
        private bool _featureOutput;
        private readonly bool _hasOutput;
        private string _status = "queued", _value = "", _messages = "";
        private DateTimeOffset? _started, _finished;
        private bool _cancelRequested, _cancelInProgress, _disposeRequested, _disposed;
        internal AcceptanceAddin.WorkbenchJobService.JobLease Workbench;
        internal string SafeTaskRoot;

        internal GpJob(string tool, string[] parameters, string project)
        {
            _tool = tool; Project = project; Token = _cancellation.Token;
            var index = tool switch {
                "analysis.Clip" or "analysis.SpatialJoin" => 2,
                "analysis.Buffer" or "management.CopyFeatures" or "management.Project" or "management.Dissolve" or "conversion.FeaturesToJSON" => 1,
                _ => -1
            };
            _hasOutput = index >= 0 && index < parameters.Length;
            if (_hasOutput) CaptureOutput(parameters[index]);
        }
        private void CaptureOutput(string path)
        {
            if (string.IsNullOrEmpty(path) || !Path.IsPathFullyQualified(path) || path.StartsWith(@"\\") || path.Contains("://")) return;
            _output = Path.GetFullPath(path);
            _featureOutput = _tool != "conversion.FeaturesToJSON" &&
                (_output.EndsWith(".shp", StringComparison.OrdinalIgnoreCase) || _output.IndexOf(".gdb" + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) >= 0);
        }
        internal bool IsRunning { get { lock (_lock) return _status == "queued" || _status == "running"; } }

        internal static bool SameFeatureSource(string existing, string output)
        {
            if (string.IsNullOrEmpty(existing) || string.IsNullOrEmpty(output) ||
                !Path.IsPathFullyQualified(existing) || !Path.IsPathFullyQualified(output)) return false;
            var a = Path.GetFullPath(existing); var b = Path.GetFullPath(output);
            if (string.Equals(a, b, StringComparison.OrdinalIgnoreCase)) return true;
            // Pro's Layer.GetPath() omits .shp for shapefile feature layers.
            // Apply this equivalence only outside file geodatabases.
            return b.EndsWith(".shp", StringComparison.OrdinalIgnoreCase) && b.IndexOf(".gdb" + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) < 0 &&
                string.Equals(a + ".shp", b, StringComparison.OrdinalIgnoreCase);
        }

        internal void Set(string status, string value, string messages)
        {
            lock (_lock)
            {
                if (!IsRunning) return; // Late cancellation/completion never rewrites a receipt.
                if (status == "running")
                {
                    if (_started.HasValue) return;
                    _started = DateTimeOffset.UtcNow; _elapsed.Start();
                }
                else if (status == "succeeded" || status == "failed" || status == "cancelled")
                {
                    _finished = DateTimeOffset.UtcNow; _elapsed.Stop();
                    // Native GP may append .shp or otherwise normalize the requested
                    // output. Prefer its actual absolute path, never a scalar count.
                    if (status == "succeeded" && _hasOutput) CaptureOutput(value);
                }
                else throw new ArgumentException("无效任务状态。");
                _status = status; _value = value ?? "";
                messages ??= ""; _messages = messages.Length > 16000 ? messages.Substring(0, 16000) : messages;
            }
        }
        internal void RequestCancel()
        {
            lock (_lock)
            {
                if (_cancelRequested) return;
                _cancelRequested = true;
                if (_disposeRequested) return;
                _cancelInProgress = true;
            }
            // SDK callbacks are external code: run them without the state lock.
            // Dispose is deferred until this cancellation lease is released.
            try
            {
                Workbench?.RequestStop();
                if (SafeTaskRoot == null) _cancellation.Cancel();
            }
            finally
            {
                bool dispose;
                lock (_lock)
                {
                    _cancelInProgress = false;
                    dispose = _disposeRequested && !_disposed;
                    if (dispose) _disposed = true;
                }
                if (dispose) _cancellation.Dispose();
            }
        }
        internal string RequireFeatureOutput(string project)
        {
            lock (_lock)
            {
                PaneState.EnsureProject(Project, project);
                if (_status != "succeeded") throw new InvalidOperationException("任务尚未成功，不能加载结果；请先查询任务状态。");
                if (!_featureOutput) throw new InvalidOperationException("该任务没有可加载的本地 GDB/SHP 要素结果；计数值和 JSON 文件不是要素图层。");
                return _output;
            }
        }
        internal JsonNode Snapshot(bool includeMessages = true)
        {
            lock (_lock) return JsonSerializer.SerializeToNode(new {
                job_id = Id, project = Project, tool = _tool, status = _status, value = _value,
                messages = includeMessages ? _messages : (_messages.Length > 512 ? _messages.Substring(0, 512) : _messages),
                cancel_requested = _cancelRequested, created_at = Created, started_at = _started, finished_at = _finished,
                elapsed_ms = _elapsed.ElapsedMilliseconds, output_path = _status == "succeeded" ? _output : null,
                can_load_output = _status == "succeeded" && _featureOutput });
        }
        public void Dispose()
        {
            bool dispose;
            lock (_lock)
            {
                _disposeRequested = true;
                dispose = !_cancelInProgress && !_disposed;
                if (dispose) _disposed = true;
            }
            if (dispose) _cancellation.Dispose();
        }
    }
}
