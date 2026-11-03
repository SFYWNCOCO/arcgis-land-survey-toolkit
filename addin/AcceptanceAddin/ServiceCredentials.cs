using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace AcceptanceAddin
{
    internal static class ServiceCredentials
    {
        internal static string CredentialPath => ProAiBridge.BasemapPreview.SettingsPath;
        internal static string AmapPoiCredentialPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "SamplingAssistant", "credentials", "amap_poi.key.dpapi");
        internal static string Load(string path = null)
        {
            if (path == null) return ProAiBridge.BasemapPreview.Load().TiandituKey;
            if (!File.Exists(path)) return null;
            var plaintext = ProtectedData.Unprotect(Convert.FromHexString(File.ReadAllText(path).Trim()), null, DataProtectionScope.CurrentUser);
            try { return Encoding.Unicode.GetString(plaintext); }
            finally { Array.Clear(plaintext, 0, plaintext.Length); }
        }
        internal static void Save(string key, string path = null)
        {
            if (string.IsNullOrWhiteSpace(key)) throw new ArgumentException("请先填写天地图密钥。");
            if (path == null)
            {
                ProAiBridge.BasemapPreview.SaveChanges(tiandituKey: key.Trim()); return;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            var bytes = Encoding.Unicode.GetBytes(key.Trim());
            byte[] protectedBytes;
            try { protectedBytes = ProtectedData.Protect(bytes, null, DataProtectionScope.CurrentUser); }
            finally { Array.Clear(bytes, 0, bytes.Length); }
            var temporary = path + ".tmp." + Guid.NewGuid().ToString("N");
            File.WriteAllText(temporary, Convert.ToHexString(protectedBytes), new UTF8Encoding(false));
            File.Move(temporary, path, true);
        }

        internal static string LoadAmapPoi() => Load(AmapPoiCredentialPath);

        internal static void SaveAmapPoi(string key)
        {
            if (string.IsNullOrWhiteSpace(key)) throw new ArgumentException("请先填写高德 Web 服务 Key。");
            Save(key, AmapPoiCredentialPath);
        }

        internal static void DeleteAmapPoi()
        {
            if (File.Exists(AmapPoiCredentialPath)) File.Delete(AmapPoiCredentialPath);
        }
    }
}
