using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ProAiBridge
{
    internal sealed class ModelSettings
    {
        public string BaseUrl { get; set; } = "https://api.openai.com/v1";
        public string Model { get; set; } = "";
        public string ApiKey { get; set; } = "";
        public int RequestTimeoutSeconds { get; set; } = 120;

        internal Uri Endpoint()
        {
            if (string.IsNullOrWhiteSpace(Model) || Model.Length > 256) throw new ArgumentException("请填写模型名称。");
            return new Uri(BaseEndpoint() + "/chat/completions");
        }
        internal Uri ModelsEndpoint() => new Uri(BaseEndpoint() + "/models");
        private string BaseEndpoint()
        {
            if (RequestTimeoutSeconds < 5 || RequestTimeoutSeconds > 600) throw new ArgumentException("请求超时须为5至600秒。");
            if (!Uri.TryCreate(BaseUrl.Trim(), UriKind.Absolute, out var uri) ||
                !(uri.Scheme == "https" || uri.Scheme == "http" && uri.IsLoopback) ||
                !string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment))
                throw new ArgumentException("API 地址必须为 HTTPS（本机回环允许 HTTP），且不能携带账号、query 或 fragment。");
            if ((ApiKey ?? "").IndexOfAny(new[] { '\r', '\n' }) >= 0) throw new ArgumentException("密钥不能包含换行。");
            var path = uri.AbsoluteUri.TrimEnd('/');
            return path.EndsWith("/chat/completions", StringComparison.OrdinalIgnoreCase) ? path.Substring(0, path.Length - "/chat/completions".Length) : path;
        }
    }

    internal static class SettingsStore
    {
        internal static string DefaultPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ProAiBridge", "settings.json");
        internal static ModelSettings Load(string path = null)
        {
            path ??= DefaultPath;
            if (!File.Exists(path)) return new ModelSettings();
            var obj = JsonNode.Parse(File.ReadAllText(path)).AsObject();
            var encrypted = (string)obj["protectedKey"] ?? "";
            var key = encrypted.Length == 0 ? "" : Encoding.UTF8.GetString(ProtectedData.Unprotect(
                Convert.FromBase64String(encrypted), null, DataProtectionScope.CurrentUser));
            return new ModelSettings { BaseUrl = (string)obj["baseUrl"] ?? "", Model = (string)obj["model"] ?? "", ApiKey = key,
                RequestTimeoutSeconds = (int?)obj["requestTimeoutSeconds"] ?? 120 };
        }

        internal static void Save(ModelSettings settings, string path = null)
        {
            settings.Endpoint();
            path ??= DefaultPath;
            var encrypted = string.IsNullOrEmpty(settings.ApiKey) ? "" : Convert.ToBase64String(ProtectedData.Protect(
                Encoding.UTF8.GetBytes(settings.ApiKey), null, DataProtectionScope.CurrentUser));
            var obj = new JsonObject { ["baseUrl"] = settings.BaseUrl.Trim(), ["model"] = settings.Model.Trim(), ["protectedKey"] = encrypted,
                ["requestTimeoutSeconds"] = settings.RequestTimeoutSeconds };
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path)));
            var temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                using (var file = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    var bytes = Encoding.UTF8.GetBytes(obj.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                    file.Write(bytes); file.Flush(true);
                }
                File.Move(temp, path, true);
            }
            finally { if (File.Exists(temp)) File.Delete(temp); }
        }
    }
}
