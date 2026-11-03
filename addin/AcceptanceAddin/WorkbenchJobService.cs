using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace AcceptanceAddin
{
    internal sealed class WorkbenchBusyException : InvalidOperationException
    { internal WorkbenchBusyException(string message) : base(message) { } }

    // One process-wide lease for plugin-initiated GIS work. No queue and no disk writer.
    internal static class WorkbenchJobService
    {
        private static readonly object Sync = new object();
        private static JobLease _active, _last;
        internal static event Action Changed;
        internal static bool IsBusy { get { lock (Sync) return _active != null; } }
        internal static JsonObject Snapshot()
        {
            lock (Sync) return (_active ?? _last)?.Snapshot() ?? new JsonObject { ["status"] = "Idle" };
        }
        internal static JobLease Begin(string title, string root = "")
        {
            JobLease job;
            lock (Sync)
            {
                if (_active != null) throw new WorkbenchBusyException("已有任务“" + _active.Title + "”正在处理，请等待或停止后再试。");
                job = new JobLease(title, root); _active = _last = job;
            }
            Notify(); return job;
        }
        private static void Notify()
        {
            foreach (Action handler in Changed?.GetInvocationList() ?? Array.Empty<Delegate>())
                try { handler(); } catch (Exception ex) { Trace.TraceError(ex.ToString()); }
        }
        internal sealed class JobLease : IDisposable
        {
            internal readonly string Id = Guid.NewGuid().ToString("N"), Title, Root;
            private string _status = "Validating", _phase = "", _detail = "", _error;
            private bool _stop, _disposed;
            internal string ObservedRunId { get; private set; }
            internal JobLease(string title, string root) { Title = title; Root = root; }
            internal string Status { get { lock (Sync) return _status; } }
            internal bool StopRequested { get { lock (Sync) return _stop; } }
            private bool Terminal => _status != "Validating" && _status != "Running" && _status != "Cancelling";
            internal void Running(string phase = "", string detail = "")
            {
                lock (Sync) { if (_disposed || Terminal) return; _phase = phase; _detail = detail; _status = _stop ? "Cancelling" : "Running"; }
                Notify();
            }
            internal void RequestStop()
            {
                lock (Sync) { if (_disposed || Terminal) return; _stop = true; _status = "Cancelling"; }
                Notify();
            }
            internal async Task MonitorSamplingAsync(string root, string previousRun, CancellationToken token)
            {
                while (!token.IsCancellationRequested)
                {
                    var receipt = await Task.Run(() => WorkbenchTaskReceipt.Read(root), token);
                    var run = (string)receipt["run_id"];
                    if (!string.IsNullOrEmpty(run) && run != previousRun)
                    {
                        ObservedRunId = run;
                        Running((string)receipt["phase"] ?? "", (string)receipt["map_progress"] ?? "");
                        if (StopRequested && (string)receipt["status"] == "Running")
                            await Task.Run(() => WorkbenchTaskReceipt.RequestStop(root, run), token);
                    }
                    await Task.Delay(500, token);
                }
            }
            internal void Finish(string status, string error = null)
            {
                if (!new[] { "Prepared", "Succeeded", "Failed", "Cancelled", "Interrupted" }.Contains(status))
                    throw new ArgumentException("无效的任务终态。");
                lock (Sync) { if (_disposed || Terminal) return; _status = status; _error = error; }
                Notify();
            }
            internal JsonObject Snapshot()
            {
                lock (Sync) return new JsonObject { ["id"] = Id, ["title"] = Title, ["root"] = Root,
                    ["status"] = _status, ["phase"] = _phase, ["detail"] = _detail, ["cancel_requested"] = _stop, ["error"] = _error };
            }
            public void Dispose()
            {
                lock (Sync)
                {
                    if (_disposed) return;
                    if (!Terminal) { _status = "Interrupted"; _error = "任务未正常结束，请核对进度后继续。"; }
                    _disposed = true;
                    if (ReferenceEquals(_active, this)) _active = null;
                }
                Notify();
            }
        }
    }

    // A read-only projection of Python's existing state/report contract.
    internal static class WorkbenchTaskReceipt
    {
        private static bool Within(string path, string root) => Path.GetFullPath(path).StartsWith(
            Path.GetFullPath(root).TrimEnd('\\', '/') + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        internal static JsonObject ReadState(string root) =>
            JsonNode.Parse(File.ReadAllText(Path.Combine(root, "task_state.json"))) as JsonObject
            ?? throw new InvalidDataException("任务状态不是有效JSON对象。");

        internal static JsonObject ReadCurrentReport(string root, JsonObject state, out string reportPath)
        {
            var run = (string)state["run_id"];
            var status = (string)state["status"];
            if (string.IsNullOrEmpty(run) || !new[] { "PREPARED", "COMPLETED", "NEEDS_CORRECTION" }.Contains(status))
                throw new InvalidDataException("当前任务尚无可核对的本轮成果。");
            var inputFingerprint = (string)state["input_fingerprint"];
            if (inputFingerprint == null || inputFingerprint.Length != 64 || inputFingerprint.Any(c => !Uri.IsHexDigit(c)))
                throw new InvalidDataException("当前任务缺少有效的 SHA-256 输入指纹，请重新准备。");
            reportPath = (string)state["report_path"];
            if (string.IsNullOrEmpty(reportPath) || !Within(reportPath, root))
                throw new InvalidDataException("当前报告不属于本次成果目录。");
            var report = JsonNode.Parse(File.ReadAllText(reportPath)) as JsonObject
                ?? throw new InvalidDataException("成果报告不是有效JSON对象。");
            if ((string)report["任务运行ID"] != run || (string)report["输入指纹"] != inputFingerprint ||
                report["设计修订号"] == null || (int)report["设计修订号"] != ((int?)state["design_revision"] ?? 0))
                throw new InvalidDataException("报告与本次运行、输入或设计修订不一致，请重新准备。");
            return report;
        }
        private static bool Alive(JsonObject state)
        {
            try
            {
                using var process = Process.GetProcessById((int?)state["pid"] ?? 0);
                return !process.HasExited && (string)state["process_created"] == process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString();
            }
            catch (ArgumentException) { return false; }
            catch (InvalidOperationException) { return false; }
            // A permission error cannot prove the worker has exited.
            catch (System.ComponentModel.Win32Exception) { return true; }
        }
        internal static JsonObject Read(string root, string expectedRun = null)
        {
            var result = new JsonObject { ["status"] = "Idle", ["can_open_result"] = false,
                ["can_open_preview"] = false,
                ["can_resume"] = false, ["business_status"] = "NEEDS_REVIEW", ["root"] = root };
            try
            {
                var path = Path.Combine(root, "task_state.json");
                if (!File.Exists(path)) return result;
                var state = ReadState(root);
                var run = (string)state["run_id"];
                if (string.IsNullOrEmpty(run)) throw new InvalidDataException("任务缺少运行标识，请重新生成预览。");
                if (expectedRun != null && run != expectedRun) throw new InvalidDataException("读到其他轮次的任务状态。");
                result["run_id"] = run;
                result["phase"] = (string)state["current_phase"];
                if (state["current_map"] is JsonObject currentMap)
                    result["map_progress"] = ((int?)currentMap["index"] ?? 0) + "/" + ((int?)currentMap["total"] ?? 0) + " " + (string)currentMap["name"];
                result["attempt"] = (int?)state["attempt"];
                var status = (string)state["status"];
                var mapped = status switch { "RUNNING" => Alive(state) ? "Running" : "Interrupted",
                    "PREPARED" => "Prepared", "COMPLETED" => "Succeeded", "CANCELLED" => "Cancelled", _ => "Failed" };
                result["status"] = mapped; result["can_resume"] = mapped != "Running";
                if (mapped != "Prepared" && mapped != "Succeeded") return result;
                var report = ReadCurrentReport(root, state, out var reportPath);
                if (report["出图"]?["technical_status"] == null) throw new InvalidDataException("图件缺少独立技术检查状态，请重新准备。");
                var technicalFailed = (string)report["技术状态"] == "FAIL" ||
                    (string)report["出图"]?["technical_status"] == "FAIL";
                if (technicalFailed && mapped != "Prepared")
                    throw new InvalidDataException("成果存在技术失败项，请核对报告后修正。");
                var outputs = report["出图"]?["outputs"] as JsonArray;
                if (outputs == null || outputs.Count == 0) throw new InvalidDataException("报告未包含有效图件。");
                foreach (var output in outputs)
                {
                    if (output is not JsonObject) throw new InvalidDataException("图件清单含无效记录。");
                    var files = new[] { "jpg", "png", "pdf" }.Select(key => (string)output[key]).Where(p => !string.IsNullOrEmpty(p)).ToArray();
                    if (files.Length == 0 || files.Any(file => !Within(file, root) || !File.Exists(file) || new FileInfo(file).Length == 0))
                        throw new InvalidDataException("报告引用的图件缺失、为空或不属于本次成果。");
                }
                result["report_path"] = reportPath;
                result["result_dir"] = Path.GetDirectoryName(reportPath);
                var summary = (string)report["运行摘要"];
                var gallery = (string)report["出图"]?["bundle"]?["html"];
                if (!string.IsNullOrEmpty(gallery) && Within(gallery, root) && File.Exists(gallery) && new FileInfo(gallery).Length > 0)
                    summary = gallery;
                if (!string.IsNullOrEmpty(summary) && Within(summary, root) && File.Exists(summary) && new FileInfo(summary).Length > 0)
                    result["summary_path"] = summary;
                result["can_open_preview"] = mapped == "Prepared";
                result["can_open_result"] = mapped == "Succeeded" || (mapped == "Prepared" && !technicalFailed);
                // Only the professional review workflow may change business acceptance.
                result["business_status"] = (string)report["业务审核状态"] ?? "NEEDS_REVIEW";
                result["visual_status"] = (string)report["视觉复核"]?["status"] ?? "NEEDS_REVIEW";
            }
            catch (Exception ex) when (ex is IOException || ex is InvalidDataException || ex is System.Text.Json.JsonException || ex is InvalidOperationException || ex is ArgumentException || ex is UnauthorizedAccessException || ex is FormatException || ex is OverflowException)
            {
                result["status"] = "Failed"; result["can_open_result"] = false; result["can_open_preview"] = false;
                result["error"] = ex.Message;
                result.Remove("report_path"); result.Remove("result_dir"); result.Remove("summary_path");
            }
            return result;
        }
        internal static void RequestStop(string root, string run)
        {
            var state = JsonNode.Parse(File.ReadAllText(Path.Combine(root, "task_state.json")));
            if ((string)state["run_id"] != run || (string)state["status"] != "RUNNING") return;
            var marker = Path.Combine(root, "cancel.request");
            var temporary = marker + "." + Guid.NewGuid().ToString("N");
            try { File.WriteAllText(temporary, run); File.Move(temporary, marker, true); }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
    }

    internal static class AcceptanceDelivery
    {
        internal static JsonNode Read(string requested)
        {
            var root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "ArcGIS");
            var dir = string.IsNullOrWhiteSpace(requested) ? Latest(root) : Path.GetFullPath(requested);
            if (string.IsNullOrWhiteSpace(dir) || !Directory.Exists(dir))
                return System.Text.Json.JsonSerializer.SerializeToNode(new { status = "UNVERIFIED", error = "未找到成果目录；请先执行 acceptance_preview 或传入 out_dir。" });
            string reportPath = null, checklistPath = null;
            var statePath = Path.Combine(dir, "task_state.json");
            var blockers = new JsonArray();
            var fail = 0; var review = 0; var maps = 0;
            try
            {
                var state = WorkbenchTaskReceipt.ReadState(dir);
                var report = WorkbenchTaskReceipt.ReadCurrentReport(dir, state, out reportPath);
                checklistPath = Path.Combine(Path.GetDirectoryName(reportPath), "检查清单.json");
                if (File.Exists(checklistPath))
                {
                    var node = JsonNode.Parse(File.ReadAllText(checklistPath));
                    var rows = node is JsonArray array ? array : node?["检查"] as JsonArray;
                    if (rows != null)
                        foreach (var row in rows)
                        {
                            var level = (string)row?["级别"];
                            if (level == "FAIL") fail++;
                            else if (level == "NEEDS_REVIEW") review++;
                            if (level == "FAIL" || level == "NEEDS_REVIEW")
                                blockers.Add(new JsonObject {
                                    ["item"] = (string)row?["项目"] ?? "未命名检查项",
                                    ["level"] = level,
                                    ["detail"] = (string)row?["证据"] ?? "",
                                    ["next"] = (string)row?["下一步"] ?? "" });
                        }
                }
                if (report?["出图"]?["outputs"] is JsonArray outputs)
                {
                    maps = outputs.Count;
                    foreach (var output in outputs)
                    {
                        var level = (string)output?["status"];
                        if (level == "FAIL") fail++;
                        else if (level == "NEEDS_REVIEW") review++;
                        if (level == "FAIL" || level == "NEEDS_REVIEW")
                            blockers.Add(new JsonObject {
                                ["item"] = "图幅 " + ((string)output?["target"] ?? (string)output?["title"] ?? ""),
                                ["level"] = level,
                                ["detail"] = (string)output?["detail"] ?? "",
                                ["next"] = "查看该图幅检查项与缩略图" });
                    }
                }
            }
            catch (Exception ex) when (ex is IOException || ex is InvalidDataException || ex is System.Text.Json.JsonException ||
                ex is InvalidOperationException || ex is ArgumentException || ex is UnauthorizedAccessException ||
                ex is FormatException || ex is OverflowException)
            { return System.Text.Json.JsonSerializer.SerializeToNode(new { status = "UNVERIFIED", out_dir = dir, error = "当前成果无法核对：" + ex.Message }); }
            var status = fail > 0 ? "FAIL" : "NEEDS_REVIEW";
            return System.Text.Json.JsonSerializer.SerializeToNode(new {
                status, out_dir = dir, report = reportPath,
                checklist = File.Exists(checklistPath) ? checklistPath : null,
                task_state = File.Exists(statePath) ? statePath : null,
                counts = new { fail, review, maps },
                blockers,
                next = status == "FAIL" ? "先修正 FAIL 项，再重新生成预览；不得正式导出。"
                    : "核对 NEEDS_REVIEW 项；正式导出仍须人工确认与签审。"
            });
        }

        private static string Latest(string root)
        {
            if (!Directory.Exists(root)) return null;
            var dirs = new List<string>();
            try
            {
                var bridgeRoot = Path.Combine(root, "ProAiBridge");
                if (Directory.Exists(bridgeRoot))
                    dirs.AddRange(Directory.GetDirectories(bridgeRoot, "acceptance_*"));
                foreach (var dir in Directory.GetDirectories(root))
                {
                    if (File.Exists(Path.Combine(dir, "一键成果报告.json")) || File.Exists(Path.Combine(dir, "检查清单.json")) ||
                        File.Exists(Path.Combine(dir, "task_state.json"))) dirs.Add(dir);
                    var sub = Path.Combine(dir, "ProAiBridge");
                    if (!Directory.Exists(sub)) continue;
                    foreach (var snapshot in Directory.GetDirectories(sub, "acceptance_*")) dirs.Add(snapshot);
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return dirs.OrderByDescending(path => Directory.GetLastWriteTimeUtc(path)).FirstOrDefault();
        }
    }
}
