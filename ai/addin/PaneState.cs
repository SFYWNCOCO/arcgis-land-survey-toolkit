using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace ProAiBridge
{
    // UI state and validation are shared by the actual pane and executable tests.
    internal sealed class PaneState
    {
        internal static string DatasetPath(string value) =>
            Uri.TryCreate(value, UriKind.Absolute, out var uri) && uri.IsFile ? uri.LocalPath : value;
        internal static readonly string[] Commands = { "help", "map", "layers", "tasks", "sampling", "analysis", "chat", "basemap", "clear", "model", "stop" };
        internal static string ParseCommand(string prompt)
        {
            var text = (prompt ?? "").Trim();
            if (!text.StartsWith("/")) return null;
            var command = text.Substring(1);
            if (!Commands.Contains(command)) throw new ArgumentException("未知命令；输入 /help 查看可用命令。");
            return command;
        }
        internal static void EnsureScopeUnchanged(System.Collections.Generic.IEnumerable<long> expected,
            System.Collections.Generic.IEnumerable<long> current, string expectedQuery, string currentQuery, string scope)
        {
            if (scope != "all" && scope != "query" && scope != "selection") throw new ArgumentException("未知处理范围。");
            if (scope == "all") return;
            if ((scope == "selection" && !(new System.Collections.Generic.HashSet<long>(expected ?? Array.Empty<long>())).SetEquals(current ?? Array.Empty<long>())) ||
                !string.Equals(expectedQuery ?? "", currentQuery ?? "", StringComparison.Ordinal))
                throw new InvalidOperationException("图层选择集或定义查询已变化；请刷新资料并重新核对处理范围，原预览不能继续使用。");
        }
        internal string OutputPath { get; private set; }
        internal JsonArray Jobs { get; private set; } = new JsonArray();
        private int _refreshing;
        private int _generation;

        internal static bool CanCancelJob(JsonNode job, string currentProject)
        {
            if (job is not JsonObject || string.IsNullOrEmpty(currentProject)) return false;
            var state = (string)job["status"];
            return !string.IsNullOrEmpty((string)job["job_id"]) &&
                string.Equals((string)job["project"], currentProject, StringComparison.OrdinalIgnoreCase) &&
                (state == "queued" || state == "running") && (bool?)job["cancel_requested"] != true;
        }

        internal static void EnsureProject(string expected, string actual)
        {
            if (!string.IsNullOrEmpty(expected) && !string.Equals(expected, actual, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("目标工程已变化；请重新列出并选择工程后再操作。");
        }

        internal static void EnsureMap(string expected, string actual)
        {
            if (!string.IsNullOrEmpty(expected) && !string.Equals(expected, actual, StringComparison.Ordinal))
                throw new InvalidOperationException("目标地图已变化；请调用 list_maps、activate_map 重新选择地图。");
        }

        internal static string[] PrepareGpParameters(string tool, string[] input, Func<string, string> resolveLayer)
        {
            var values = (string[])input.Clone();
            var minimum = tool == "management.GetCount" ? 1 : new[] { "analysis.Buffer", "analysis.Clip", "analysis.SpatialJoin", "management.Project" }.Contains(tool) ? 3 : 2;
            if (values.Length < minimum) throw new ArgumentException("地理处理参数不完整。");
            var twoInputs = tool == "analysis.Clip" || tool == "analysis.SpatialJoin";
            var inputs = twoInputs ? new[] { 0, 1 } : new[] { 0 };
            foreach (var index in inputs)
            {
                var raw = values[index];
                if (Path.IsPathFullyQualified(raw)) values[index] = Path.GetFullPath(raw);
                else if (Uri.TryCreate(raw, UriKind.Absolute, out var uri) && uri.Scheme == "https" && uri.UserInfo.Length == 0) values[index] = uri.AbsoluteUri;
                else values[index] = resolveLayer(raw);
            }
            if (tool != "management.GetCount")
            {
                var output = twoInputs ? 2 : 1;
                if (!Path.IsPathFullyQualified(values[output]) || values[output].StartsWith(@"\\") || values[output].Contains("://"))
                    throw new ArgumentException("输出数据必须使用本机绝对路径，避免切换工程时写入错误位置。");
                values[output] = Path.GetFullPath(values[output]);
                if (Path.GetFileName(values[output]).IndexOfAny(Path.GetInvalidFileNameChars()) >= 0) throw new ArgumentException("输出数据名无效。");
            }
            return values;
        }

        internal bool RecordOutput(string tool, JsonNode result)
        {
            if (tool != "export_map" && tool != "export_layout") return false;
            var raw = (string)result?["path"];
            if (string.IsNullOrEmpty(raw) || !Path.IsPathFullyQualified(raw) || raw.StartsWith(@"\\")) return false;
            var path = Path.GetFullPath(raw);
            if (!new[] { ".png", ".pdf" }.Contains(Path.GetExtension(path).ToLowerInvariant()) || !File.Exists(path) || new FileInfo(path).Length == 0)
                return false;
            OutputPath = path;
            return true;
        }

        internal void Reset() { Interlocked.Increment(ref _generation); OutputPath = null; Jobs = new JsonArray(); }

        internal async Task<bool> RefreshJobsAsync(Func<CancellationToken, Task<JsonNode>> fetch, CancellationToken token)
        {
            if (Interlocked.CompareExchange(ref _refreshing, 1, 0) != 0) return false;
            try
            {
                var generation = _generation;
                token.ThrowIfCancellationRequested();
                var reply = await fetch(token);
                token.ThrowIfCancellationRequested();
                if (generation != _generation) return false;
                if (reply?["latest_export"] is JsonObject exported) RecordOutput((string)exported["tool"], exported);
                var jobs = reply?["jobs"] as JsonArray ?? throw new InvalidOperationException("任务列表响应缺少 jobs。");
                if (jobs.ToJsonString() == Jobs.ToJsonString()) return false;
                Jobs = ToolProtocol.Copy(jobs).AsArray();
                return true;
            }
            finally { Volatile.Write(ref _refreshing, 0); }
        }

        internal static JsonObject Diagnose(string home, bool bridgeReady, int pid, string project)
        {
            var config = Path.Combine(home, "profiles", "desktop", "cordis.patch.yml");
            var text = File.Exists(config) ? File.ReadAllText(config) : "";
            var saved = Regex.IsMatch(text, @"(?m)^[ \t]*(?:-[ \t]*)?id[ \t]*:[ \t]*(['""]?)pro-ai-bridge-mcp\1[ \t\r]*(?:#.*)?$");
            string running;
            try
            {
                var processes = Process.GetProcessesByName("DeepSeek Harness");
                running = processes.Length == 0 ? "stopped" : "running";
                foreach (var process in processes) process.Dispose();
            }
            catch { running = "unknown"; }
            return new JsonObject { ["pro_bridge"] = bridgeReady ? "ready" : "starting", ["process_id"] = pid,
                ["project"] = project ?? "", ["dsh_configuration"] = saved ? "saved" : "missing",
                ["dsh_process"] = running, ["dsh_connection"] = "not_verified",
                ["hint"] = "DSH 配置/进程状态与实际连接分别检查；请在 DSH 中调用 get_status 验证模型链路。" };
        }
    }
}
