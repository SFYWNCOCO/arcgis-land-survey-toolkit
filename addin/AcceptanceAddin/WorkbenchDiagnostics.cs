using System;
using System.ComponentModel;
using System.IO;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace AcceptanceAddin
{
    internal static class WorkbenchDiagnostics
    {
        internal static string Redact(string text, string[] secrets = null)
        {
            text ??= "";
            foreach (var secret in secrets ?? Array.Empty<string>())
                if (!string.IsNullOrEmpty(secret)) text = text.Replace(secret, "[已隐藏]", StringComparison.Ordinal);
            text = Regex.Replace(text, @"(?i)(\b(?:tk|key|token|password|api[_-]?key)[""']?\s*[=:]\s*)(?:""(?:\\.|[^""\\])*""|'(?:\\.|[^'\\])*')", "$1\"[已隐藏]\"");
            text = Regex.Replace(text, @"(?i)(\b(?:tk|key|token|password|api[_-]?key)[""']?\s*[=:]\s*)[^\s&""',;]+", "$1[已隐藏]");
            return Regex.Replace(text, @"(?i)(Bearer\s+)[\w.\-+/=]+", "$1[已隐藏]");
        }
        internal static JsonObject Error(Exception exception, string context, string[] secrets = null)
        {
            var detail = Redact(exception.ToString(), secrets);
            var code = "GP_FAILED"; var message = "处理失败，请查看诊断信息后重试。";
            if (exception is WorkbenchBusyException)
            { code = "JOB_BUSY"; message = exception.Message; }
            else if (exception is Win32Exception win32 && win32.NativeErrorCode == 2)
            {
                code = context == "bridge" ? "BRIDGE_UNAVAILABLE" : context == "result" ? "RESULT_MISSING" : "ENV_EXECUTABLE_MISSING";
                message = context == "bridge" ? "Pro实时桥接不可用，请打开Pro并确认插件已加载。" : context == "result" ? "成果文件已不存在，请重新生成。" : "找不到运行程序，请运行环境检查。";
            }
            else if (exception is FileNotFoundException || exception is DirectoryNotFoundException)
            { code = context == "toolbox" ? "ENV_TOOLBOX_MISSING" : context == "result" ? "RESULT_MISSING" : "INPUT_MISSING"; message = "找不到所需文件，请核对路径或重新安装插件。"; }
            else if (exception is UnauthorizedAccessException)
            { code = "PATH_DENIED"; message = "当前目录无访问权限，请选择可写目录。"; }
            else if (exception is System.Net.Http.HttpRequestException quota && quota.StatusCode == System.Net.HttpStatusCode.TooManyRequests ||
                Regex.IsMatch(exception.Message, @"(?i)(?:HTTP\s*|infocode[""']?\s*[:=]\s*[""']?|高德接口返回失败（?)(429|10003|10004|10044)\b"))
            { code = "MAP_QUOTA"; message = "地图服务调用达到限制，请稍后重试或核对配额。"; }
            else if (exception is System.Net.Http.HttpRequestException auth && (auth.StatusCode == System.Net.HttpStatusCode.Unauthorized || auth.StatusCode == System.Net.HttpStatusCode.Forbidden) ||
                Regex.IsMatch(exception.Message, @"(?i)(?:HTTP\s*|infocode[""']?\s*[:=]\s*[""']?|高德接口返回失败（?)(401|403|10001|10002|10009)\b"))
            { code = "MAP_AUTH"; message = "地图服务授权无效或无权限，请核对所选服务的密钥。"; }
            else if (exception is InvalidDataException)
            { code = "RESULT_INVALID"; message = Redact(exception.Message, secrets); }
            else if (exception is OperationCanceledException)
            { code = "CANCELLED"; message = "任务已停止，已完成阶段保留。"; }
            return new JsonObject { ["code"] = code, ["message"] = message, ["context"] = context, ["detail"] = detail };
        }
        internal static void CheckResources(string toolbox)
        {
            if (!File.Exists(toolbox)) throw new FileNotFoundException("插件工具箱不存在。", toolbox);
            var root = Directory.GetParent(Path.GetDirectoryName(toolbox)).FullName;
            foreach (var relative in new[] { "acceptance/oneclick.py", "acceptance/task_state.py", "acceptance/workflow.py", "acceptance/map_worker.py", "acceptance/map_export.py", "templates/补充耕地鉴定图_A4.pagx" })
                if (!File.Exists(Path.Combine(root, relative))) throw new FileNotFoundException("插件资源不完整，请重新安装。", relative);
        }
    }
}
