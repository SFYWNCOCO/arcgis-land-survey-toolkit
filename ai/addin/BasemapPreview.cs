using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace ProAiBridge
{
    internal sealed class BasemapSettings
    {
        public string AmapKey { get; set; } = "";
        public string AmapSecurityCode { get; set; } = "";
        public string TiandituKey { get; set; } = "";
    }

    // Owns only browser previews and their credentials; never writes GIS geometry.
    internal sealed class BasemapPreview : IDisposable
    {
        private readonly TcpListener _listener;
        private readonly CancellationTokenSource _stop;
        private readonly SemaphoreSlim _tileSlots = new SemaphoreSlim(2, 2);
        private readonly string _source, _tiandituKey;
        private readonly string _html;
        private static readonly HttpClient TileClient = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false })
        { Timeout = Timeout.InfiniteTimeSpan };
        private static BasemapPreview _active;
        private static readonly System.Collections.Concurrent.ConcurrentDictionary<string, BasemapPreview> ActiveMaps =
            new System.Collections.Concurrent.ConcurrentDictionary<string, BasemapPreview>(StringComparer.Ordinal);
        internal string Token { get; } = Convert.ToHexString(RandomNumberGenerator.GetBytes(24));
        internal string Url { get; }
        internal static string SettingsPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ProAiBridge", "basemaps.json");

        internal static readonly string[] Sources = {
            "tianditu_image", "tianditu_vector", "tianditu_terrain", "amap_satellite", "amap_vector", "amap_district" };
        // 浏览器预览只支持可转发瓦片的来源；高德行政区划区图以矢量图层加载，不做网页预览。
        internal static readonly string[] PreviewSources = {
            "tianditu_image", "tianditu_vector", "tianditu_terrain", "amap_satellite", "amap_vector" };
        internal static string Label(string id) => id switch {
            "amap_vector" => "高德地图", "amap_satellite" => "高德卫星影像",
            "tianditu_vector" => "天地图矢量", "tianditu_image" => "天地图影像", "tianditu_terrain" => "天地图地形晕渲",
            "amap_district" => "高德行政区划区图",
            _ => throw new ArgumentException("未知底图来源。") };
        internal static bool IsGcj02(string source) => source != null && source.StartsWith("amap_", StringComparison.Ordinal);
        internal static bool IsTianditu(string source) => source != null && source.StartsWith("tianditu_", StringComparison.Ordinal);
        // 只有高德瓦片快照在 Python 侧做 GCJ-02→CGCS2000 逐像素纠偏；区图界线保持 GCJ-02 参考值。
        internal static string CorrectionLabel(string source) => source switch {
            "amap_satellite" or "amap_vector" => "GCJ02_TO_CGCS2000", _ => "NONE" };

        internal static JsonObject Catalog(BasemapSettings settings, bool amapPoiReady = false)
        {
            settings ??= new BasemapSettings();
            var rows = new JsonArray();
            foreach (var id in Sources)
            {
                var tianditu = IsTianditu(id);
                var district = id == "amap_district";
                // 天地图要 Key；高德瓦片无需 Key；区图需要高德 Web 服务（POI）Key。
                var ready = tianditu ? !string.IsNullOrWhiteSpace(settings.TiandituKey) : district ? amapPoiReady : true;
                var status = district || !tianditu ? (ready ? "READY_REFERENCE_ONLY" : "NEEDS_CONFIGURATION")
                    : ready ? "CONFIGURED_NOT_VERIFIED" : "NEEDS_CONFIGURATION";
                var hint = tianditu
                    ? ready ? "可用 add_basemap source=" + id + " 加载：mode=raster 生成快照，mode=live 建会话底图。"
                        : "先在底图设置填写天地图 Key。"
                    : district
                        ? ready ? "可用 add_basemap source=amap_district 加载行政界线与驻地注记（参考界线，正式报件须官方界线）。"
                            : "先在采样助手填写高德 Web 服务 Key（POI Key），再加载区图。"
                        : "可用 add_basemap source=" + id + " 生成 GCJ-02 逐像素纠偏后的本地快照（参考底图，不是 CGCS2000 正式成果）。";
                rows.Add(new JsonObject {
                    ["id"] = id, ["label"] = Label(id), ["status"] = status,
                    ["coordinate_system"] = tianditu ? "CGCS2000 / Web Mercator"
                        : district ? "GCJ-02（参考界线）" : "GCJ-02（纠偏后 CGCS2000）",
                    ["can_load_into_map"] = ready,
                    ["map_mode"] = district ? "vector_layers" : tianditu ? "raster_or_live" : "raster_only_corrected",
                    ["correction"] = CorrectionLabel(id),
                    ["positional_accuracy_status"] = "UNVERIFIED",
                    ["purpose"] = district ? "map_layer" : "map_layer_or_browser_preview",
                    ["source_gsd_status"] = "UNVERIFIED",
                    ["map_layer_hint"] = hint });
            }
            return new JsonObject { ["sources"] = rows, ["authorized_services"] = "采样助手：授权 XYZ/WMTS 服务 JSON 或 ArcGIS 缓存 MapServer；阿里云/单位服务需提供实际地址与授权。" };
        }

        internal static BasemapSettings Load(string path = null)
        {
            path ??= SettingsPath;
            if (!File.Exists(path)) return new BasemapSettings();
            var bytes = ProtectedData.Unprotect(Convert.FromBase64String(File.ReadAllText(path)), null, DataProtectionScope.CurrentUser);
            return JsonSerializer.Deserialize<BasemapSettings>(bytes) ?? throw new ArgumentException("底图配置为空。");
        }
        internal static void Save(BasemapSettings settings, string path = null)
        {
            foreach (var value in new[] { settings.AmapKey, settings.AmapSecurityCode, settings.TiandituKey })
                if (value == null || value.Length > 1024 || value.Any(char.IsControl)) throw new ArgumentException("底图密钥格式无效。");
            path ??= SettingsPath;
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path)));
            var temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temp, Convert.ToBase64String(ProtectedData.Protect(JsonSerializer.SerializeToUtf8Bytes(settings), null, DataProtectionScope.CurrentUser)));
                File.Move(temp, path, true);
            }
            finally { if (File.Exists(temp)) File.Delete(temp); }
        }
        private static readonly object SettingsGate = new object();
        internal static void SaveChanges(string amapKey = null, string amapSecurityCode = null, string tiandituKey = null, string path = null)
        {
            lock (SettingsGate)
            {
                var value = Load(path);
                if (amapKey != null) value.AmapKey = amapKey;
                if (amapSecurityCode != null) value.AmapSecurityCode = amapSecurityCode;
                if (tiandituKey != null) value.TiandituKey = tiandituKey;
                Save(value, path);
            }
        }

        internal static BasemapPreview Start(string source, double longitude, double latitude, BasemapSettings settings)
        {
            if (source == "amap_district") throw new ArgumentException("行政区划区图不支持浏览器预览，请用 add_basemap 加载矢量图层。");
            Label(source);
            if (!double.IsFinite(longitude) || !double.IsFinite(latitude) || longitude < -180 || longitude > 180 || latitude < -85 || latitude > 85)
                throw new ArgumentException("预览中心须为有效 WGS84 经度、纬度（纬度限±85度）。");
            var amap = IsGcj02(source);
            if (amap && (string.IsNullOrWhiteSpace(settings.AmapKey) || string.IsNullOrWhiteSpace(settings.AmapSecurityCode)))
                throw new ArgumentException("请在底图设置中填写高德 JS API Key 和安全密钥。");
            if (!amap && string.IsNullOrWhiteSpace(settings.TiandituKey)) throw new ArgumentException("请在底图设置中填写天地图 Key。");
            return new BasemapPreview(source, longitude, latitude, settings);
        }

        internal static JsonNode Open(string source, double longitude, double latitude)
        {
            var preview = Start(source, longitude, latitude, Load());
            try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(preview.Url) { UseShellExecute = true }); }
            catch { preview.Dispose(); throw new InvalidOperationException("无法打开浏览器预览。"); }
            Interlocked.Exchange(ref _active, preview)?.Dispose();
            return new JsonObject { ["status"] = "PAGE_OPENED", ["source"] = source, ["label"] = Label(source),
                ["purpose"] = "browser_preview", ["provider_loading"] = "UNVERIFIED", ["expires_minutes"] = 30,
                ["source_gsd_status"] = "UNVERIFIED", ["positional_accuracy_status"] = "UNVERIFIED" };
        }
        internal static void CloseActive() => Interlocked.Exchange(ref _active, null)?.Dispose();

        internal static BasemapPreview StartMap(string mapKey, string source, BasemapSettings settings)
        {
            if (string.IsNullOrWhiteSpace(mapKey)) throw new ArgumentException("地图标识不能为空。", nameof(mapKey));
            Label(source);
            if (!IsTianditu(source))
                throw new ArgumentException("地图会话底图只支持天地图影像、矢量或地形晕渲。");
            if (string.IsNullOrWhiteSpace(settings.TiandituKey)) throw new ArgumentException("请在底图设置中填写天地图 Key。");
            var proxy = new BasemapPreview(source, 0, 0, settings, TimeSpan.FromHours(12));
            if (ActiveMaps.TryGetValue(mapKey, out var previous)) previous.Dispose();
            ActiveMaps[mapKey] = proxy;
            return proxy;
        }

        internal static void CloseMap(string mapKey)
        {
            if (ActiveMaps.TryRemove(mapKey, out var proxy)) proxy.Dispose();
        }

        internal static void CloseAllMaps()
        {
            foreach (var key in ActiveMaps.Keys) CloseMap(key);
        }

        internal string MapTemplate => Url + "/xyz/" + TileLayers(_source).Base + "/{z}/{y}/{x}.jpg";
        internal string LabelTemplate => Url + "/xyz/" + TileLayers(_source).Annotation + "/{z}/{y}/{x}.jpg";

        private static (string Base, string Annotation) TileLayers(string source) => source switch {
            "tianditu_vector" => ("vec", "cva"),
            "tianditu_terrain" => ("ter", "cta"),
            _ => ("img", "cia") };

        private BasemapPreview(string source, double longitude, double latitude, BasemapSettings settings)
            : this(source, longitude, latitude, settings, TimeSpan.FromMinutes(30)) { }

        private BasemapPreview(string source, double longitude, double latitude, BasemapSettings settings, TimeSpan? lifetime)
        {
            _stop = lifetime.HasValue ? new CancellationTokenSource(lifetime.Value) : new CancellationTokenSource();
            _source = source;
            _tiandituKey = settings.TiandituKey;
            _listener = new TcpListener(IPAddress.Loopback, 0); _listener.Start(64);
            Url = "http://127.0.0.1:" + ((IPEndPoint)_listener.LocalEndpoint).Port + "/" + Token;
            _html = BuildHtml(source, longitude, latitude, settings, Token);
            _stop.Token.Register(() => _listener.Stop());
            _ = ServeAsync();
        }
        private async Task ServeAsync()
        {
            try
            {
                while (!_stop.IsCancellationRequested)
                {
                    var client = await _listener.AcceptTcpClientAsync(_stop.Token);
                    _ = ServeClientAsync(client);
                }
            }
            catch (Exception ex) when (ex is OperationCanceledException || ex is SocketException || ex is ObjectDisposedException) { }
        }
        private async Task ServeClientAsync(TcpClient client)
        {
            using (client)
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(_stop.Token))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(30));
                try
                {
                    using var stream = client.GetStream();
                    var request = new StringBuilder(); var buffer = new byte[1];
                    while (request.Length < 8192 && !request.ToString().EndsWith("\r\n\r\n", StringComparison.Ordinal))
                    {
                        if (await stream.ReadAsync(buffer.AsMemory(), deadline.Token) == 0) return;
                        request.Append((char)buffer[0]);
                    }
                    var lines = request.ToString().Split("\r\n");
                    var parts = lines.FirstOrDefault()?.Split(' ') ?? Array.Empty<string>();
                    var endpoint = (IPEndPoint)_listener.LocalEndpoint;
                    var expectedHost = "127.0.0.1:" + endpoint.Port;
                    var expectedOrigin = "http://" + expectedHost;
                    var host = lines.FirstOrDefault(line => line.StartsWith("Host:", StringComparison.OrdinalIgnoreCase))?.Substring(5).Trim();
                    var originLine = lines.FirstOrDefault(line => line.StartsWith("Origin:", StringComparison.OrdinalIgnoreCase));
                    var origin = originLine?.Substring(7).Trim();
                    var validRequest = parts.Length == 3 && parts[0] == "GET" && parts[2] == "HTTP/1.1" &&
                        string.Equals(host, expectedHost, StringComparison.OrdinalIgnoreCase) &&
                        (origin == null || string.Equals(origin, expectedOrigin, StringComparison.OrdinalIgnoreCase));
                    if (validRequest && parts[1] == "/" + Token)
                    {
                        await WriteResponseAsync(stream, 200, "OK", "text/html; charset=utf-8", Encoding.UTF8.GetBytes(_html),
                            "no-store", deadline.Token);
                        return;
                    }
                    if (validRequest && TryParseTileRoute(parts[1], Token, _source, out var layer, out var zoom, out var row, out var column))
                    {
                        await ProxyTileAsync(stream, layer, zoom, row, column, deadline.Token);
                        return;
                    }
                    await WriteResponseAsync(stream, validRequest ? 404 : 403, validRequest ? "Not Found" : "Forbidden",
                        "text/plain; charset=utf-8", Encoding.UTF8.GetBytes(validRequest ? "Not found" : "Forbidden"),
                        "no-store", deadline.Token);
                }
                catch (Exception ex) when (ex is IOException || ex is OperationCanceledException || ex is SocketException || ex is HttpRequestException || ex is ObjectDisposedException) { }
            }
        }
        internal static bool TryParseTileRoute(string path, string token, string source,
            out string layer, out int zoom, out int row, out int column)
        {
            layer = null; zoom = row = column = 0;
            if (!IsTianditu(source) || string.IsNullOrEmpty(path) || string.IsNullOrEmpty(token)) return false;
            var names = TileLayers(source);
            var segments = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
            if (segments.Length != 6 || segments[0] != token) return false;
            if (segments[1] == "tile")
            {
                var allowed = segments[2] == names.Base || segments[2] == names.Annotation;
                if (!allowed || !int.TryParse(segments[3], NumberStyles.None, CultureInfo.InvariantCulture, out zoom) || zoom < 3 || zoom > 18 ||
                    !int.TryParse(segments[4], NumberStyles.None, CultureInfo.InvariantCulture, out row) ||
                    !int.TryParse(segments[5], NumberStyles.None, CultureInfo.InvariantCulture, out column)) return false;
                var size = 1 << zoom;
                if (row < 0 || row >= size || column < 0 || column >= size) return false;
                layer = segments[2];
                return string.Equals(path, "/" + token + "/tile/" + layer + "/" + zoom.ToString(CultureInfo.InvariantCulture) + "/" +
                    row.ToString(CultureInfo.InvariantCulture) + "/" + column.ToString(CultureInfo.InvariantCulture), StringComparison.Ordinal);
            }
            string layerName, zoomText, rowText, columnText;
            if (segments[1] == "xyz")
            {
                layerName = segments[2]; zoomText = segments[3];
                rowText = WithoutImageExtension(segments[4]); columnText = WithoutImageExtension(segments[5]);
            }
            else if (segments[1] == "tiles")
            {
                layerName = segments[2]; zoomText = segments[3];
                columnText = WithoutImageExtension(segments[4]); rowText = WithoutImageExtension(segments[5]);
            }
            else return false;
            var allowedLayer = layerName == names.Base || layerName == names.Annotation;
            if (!allowedLayer || !int.TryParse(zoomText, NumberStyles.None, CultureInfo.InvariantCulture, out zoom) || zoom < 3 || zoom > 18 ||
                !int.TryParse(rowText, NumberStyles.None, CultureInfo.InvariantCulture, out row) ||
                !int.TryParse(columnText, NumberStyles.None, CultureInfo.InvariantCulture, out column)) return false;
            var limit = 1 << zoom;
            if (row < 0 || row >= limit || column < 0 || column >= limit) return false;
            layer = layerName;
            return true;
        }
        private static string WithoutImageExtension(string value) =>
            value.EndsWith(".jpg", StringComparison.OrdinalIgnoreCase) || value.EndsWith(".png", StringComparison.OrdinalIgnoreCase)
                ? value.Substring(0, value.Length - 4) : value;
        private async Task ProxyTileAsync(NetworkStream stream, string layer, int zoom, int row, int column, CancellationToken token)
        {
            await _tileSlots.WaitAsync(token);
            try
            {
                var uri = "https://t0.tianditu.gov.cn/" + layer + "_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=" + layer +
                    "&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX=" + zoom.ToString(CultureInfo.InvariantCulture) +
                    "&TILEROW=" + row.ToString(CultureInfo.InvariantCulture) + "&TILECOL=" + column.ToString(CultureInfo.InvariantCulture) +
                    "&tk=" + Uri.EscapeDataString(_tiandituKey);
                using var request = new HttpRequestMessage(HttpMethod.Get, uri);
                using var response = await TileClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token);
                var type = response.Content.Headers.ContentType?.MediaType ?? "";
                if (!response.IsSuccessStatusCode)
                {
                    await WriteResponseAsync(stream, 502, "Bad Gateway", "text/plain; charset=utf-8",
                        Encoding.UTF8.GetBytes("Tile provider request failed"), "no-store", token);
                    return;
                }
                if (response.Content.Headers.ContentLength > 2_000_000)
                    throw new InvalidOperationException("底图瓦片超过大小限制。");
                var body = await ReadTileAsync(response.Content, token);
                var browserType = ImageTileType(body) ?? throw new InvalidOperationException("底图服务未返回有效图片瓦片。");
                await WriteResponseAsync(stream, 200, "OK", browserType, body, "private, max-age=300", token);
            }
            catch (Exception ex) when (ex is HttpRequestException || ex is TimeoutException || ex is InvalidOperationException)
            {
                await WriteResponseAsync(stream, 502, "Bad Gateway", "text/plain; charset=utf-8",
                    Encoding.UTF8.GetBytes("Tile provider request failed"), "no-store", token);
            }
            finally { _tileSlots.Release(); }
        }
        private static async Task<byte[]> ReadTileAsync(HttpContent content, CancellationToken token)
        {
            using var input = await content.ReadAsStreamAsync(token);
            using var output = new MemoryStream();
            var buffer = new byte[8192]; int count;
            while ((count = await input.ReadAsync(buffer.AsMemory(), token)) > 0)
            {
                if (output.Length + count > 2_000_000) throw new InvalidOperationException("底图瓦片超过大小限制。");
                output.Write(buffer, 0, count);
            }
            return output.ToArray();
        }
        // 缓存键的范围精度：米制快照 1 位小数（≈0.1 m）足够；区图范围是 WGS84 度，
        // 1 位小数≈11 km 会把平移后的视图误判为同一范围，必须用 1e-6 度级。
        internal const int ExtentDecimals = 1;
        internal const int DistrictExtentDecimals = 6;
        internal static string SnapshotCacheKey(int wkid, double[] extent, string keyHash, bool labels, int decimals = ExtentDecimals)
        {
            var digits = Math.Clamp(decimals, 0, 15);
            var format = "F" + digits.ToString(CultureInfo.InvariantCulture);
            return wkid.ToString(CultureInfo.InvariantCulture) + "|" + string.Join(",",
                (extent ?? Array.Empty<double>()).Select(value =>
                    Math.Round(value, digits).ToString(format, CultureInfo.InvariantCulture))) + "|" + (keyHash ?? "") +
                "|" + (labels ? "labels" : "plain");
        }

        internal static string ImageTileType(byte[] body)
        {
            if (body.Length >= 8 && body[0] == 137 && body[1] == 80 && body[2] == 78 && body[3] == 71 &&
                body[4] == 13 && body[5] == 10 && body[6] == 26 && body[7] == 10) return "image/png";
            if (body.Length >= 3 && body[0] == 255 && body[1] == 216 && body[2] == 255) return "image/jpeg";
            return null;
        }
        private static async Task WriteResponseAsync(NetworkStream stream, int status, string reason, string contentType,
            byte[] body, string cache, CancellationToken token)
        {
            var headers = Encoding.ASCII.GetBytes("HTTP/1.1 " + status + " " + reason + "\r\nContent-Type: " + contentType +
                "\r\nCache-Control: " + cache + "\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\nX-Frame-Options: DENY\r\nConnection: close\r\nContent-Length: " + body.Length + "\r\n\r\n");
            await stream.WriteAsync(headers.AsMemory(), token);
            await stream.WriteAsync(body.AsMemory(), token);
        }
        public void Dispose() { _stop.Cancel(); _listener.Stop(); }

        private static string BuildHtml(string source, double lon, double lat, BasemapSettings settings, string token)
        {
            // JSON's default encoder escapes HTML metacharacters, including closing script tags.
            var amap = IsGcj02(source);
            var image = source == "amap_satellite" || source == "tianditu_image";
            var config = JsonSerializer.Serialize(new { source, lon, lat, zoom = image ? 18 : 16,
                minZoom = 3, maxZoom = amap ? 20 : 18, key = amap ? settings.AmapKey : "",
                code = amap ? settings.AmapSecurityCode : "", tilePath = amap ? "" : "/" + token + "/tile" });
            return @"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>底图预览</title><style>html,body{height:100%;margin:0;font:14px system-ui}body{display:flex;flex-direction:column;color:#17283b}header{display:flex;align-items:center;flex-wrap:wrap;gap:10px 20px;padding:10px 16px;background:white;border-bottom:1px solid #dbe2e9}#state{flex:1;min-width:180px;font-size:14px}nav{display:flex;align-items:center;gap:6px}button{height:36px;padding:0 12px;border:1px solid #cad3dd;border-radius:6px;background:white;color:inherit;font:inherit;cursor:pointer}button:hover{background:#eef4fa}button:disabled{opacity:.4;cursor:default}#zoomIn,#zoomOut{width:36px;padding:0;font-size:22px}#level{min-width:42px;text-align:center;font-variant-numeric:tabular-nums}#labelControl{display:flex;align-items:center;gap:4px;white-space:nowrap;margin-left:6px}#accuracy{position:relative;font-size:13px;color:#536579}summary{cursor:pointer;white-space:nowrap}#accuracy p{position:absolute;right:0;top:22px;z-index:1000;width:260px;margin:0;padding:14px;background:white;box-shadow:0 4px 20px #17283b33;border-radius:8px;line-height:1.7}#map{flex:1;min-height:0;overflow:hidden;position:relative;background:#edf0f3;touch-action:none}#tiles img{position:absolute;width:256px;height:256px;user-select:none;pointer-events:none}@media(max-width:600px){header{padding:8px;gap:8px}#state{flex-basis:100%}nav{flex:1}#reset{padding:0 8px}}</style>
<header><b id='state' role='status' aria-live='polite'>正在连接底图…</b><nav aria-label='地图操作'><button id='zoomOut' aria-label='缩小' disabled>−</button><output id='level' aria-label='地图层级'></output><button id='zoomIn' aria-label='放大' disabled>+</button><button id='reset' aria-label='回到打开位置' disabled>复位</button><label id='labelControl'><input id='labels' type='checkbox' checked disabled>地名道路</label></nav><details id='accuracy' data-status='UNVERIFIED'><summary>精度说明</summary><p>放大层级不代表原始影像分辨率。拍摄日期、原始分辨率和位置精度尚未核实，底图供浏览参考。</p></details></header><div id='map' aria-label='在线底图'><div id='tiles'></div></div>
<script>const cfg=" + config + @";
const state=document.getElementById('state');
const zoomIn=document.getElementById('zoomIn'),zoomOut=document.getElementById('zoomOut'),reset=document.getElementById('reset'),labels=document.getElementById('labels'),level=document.getElementById('level');
const clamp=z=>Math.max(cfg.minZoom,Math.min(cfg.maxZoom,z));
function updateZoom(z){level.textContent=Math.round(z)+'级';zoomIn.disabled=z>=cfg.maxZoom;zoomOut.disabled=z<=cfg.minZoom;}
if(cfg.source.startsWith('amap_')){
 window._AMapSecurityConfig={securityJsCode:cfg.code};
 const script=document.createElement('script');script.src='https://webapi.amap.com/maps?v=2.0&key='+encodeURIComponent(cfg.key);
 script.onerror=()=>state.textContent='高德加载失败，请检查网络、Key 与域名白名单';
 script.onload=()=>{if(!window.AMap){state.textContent='高德 SDK 未就绪';return;}
  AMap.convertFrom([cfg.lon,cfg.lat],'gps',(status,result)=>{
   if(status!=='complete'||!result.locations?.length){state.textContent='高德坐标转换失败，请检查 Key 权限';return;}
   const satellite=cfg.source==='amap_satellite';
   const origin=result.locations[0],road=satellite?new AMap.TileLayer.RoadNet():null;
   const map=new AMap.Map('map',{center:origin,zoom:cfg.zoom,zooms:[cfg.minZoom,cfg.maxZoom],resizeEnable:true,layers:satellite?[new AMap.TileLayer.Satellite(),road]:[AMap.createDefaultLayer()]});
   zoomIn.onclick=()=>map.setZoom(clamp(map.getZoom()+1));zoomOut.onclick=()=>map.setZoom(clamp(map.getZoom()-1));
   reset.onclick=()=>map.setZoomAndCenter(cfg.zoom,origin);
   labels.disabled=false;labels.onchange=()=>labels.checked?road.show():road.hide();
   if(!satellite)document.getElementById('labelControl').style.display='none';
   reset.disabled=false;updateZoom(map.getZoom());map.on('zoomchange',()=>updateZoom(map.getZoom()));
   map.on('complete',()=>state.textContent=(satellite?'高德卫星影像':'高德地图')+' · GCJ-02');
   map.on('error',()=>state.textContent='高德地图请求失败，请检查服务授权');
  });};document.head.appendChild(script);
}else{
 const view=document.getElementById('map'),tiles=document.getElementById('tiles');let z=cfg.zoom,lon=cfg.lon,lat=cfg.lat,drag=null,epoch=0;
 const names={'tianditu_image':['img','cia'],'tianditu_vector':['vec','cva'],'tianditu_terrain':['ter','cta']}[cfg.source]||['vec','cva'],base=names[0],annotation=names[1];
 const title=cfg.source==='tianditu_image'?'天地图影像':cfg.source==='tianditu_terrain'?'天地图地形晕渲':'天地图矢量';
 const xy=()=>{const n=2**z;return[(lon+180)/360*n,(1-Math.asinh(Math.tan(lat*Math.PI/180))/Math.PI)/2*n];};
 function move(x,y){const n=2**z;lon=((x/n*360-180+540)%360)-180;lat=Math.max(-85,Math.min(85,Math.atan(Math.sinh(Math.PI*(1-2*y/n)))*180/Math.PI));}
 function draw(){const current=++epoch;tiles.replaceChildren();const [cx,cy]=xy(),w=view.clientWidth,h=view.clientHeight,n=2**z;let loaded=0,failed=0,total=0;
  updateZoom(z);state.textContent='天地图加载中…';
  for(let y=Math.floor(cy-h/512);y<=Math.floor(cy+h/512);y++)for(let x=Math.floor(cx-w/512);x<=Math.floor(cx+w/512);x++){
   if(y<0||y>=n)continue;
   for(const layer of (labels.checked?[base,annotation]:[base])){const tile=document.createElement('img');total++;tile.draggable=false;tile.style.left=(x-cx)*256+w/2+'px';tile.style.top=(y-cy)*256+h/2+'px';
    tile.onload=()=>{if(current!==epoch)return;loaded++;state.textContent=title+' · '+loaded+'/'+total+' 瓦片'+(failed?' · 有瓦片失败':'');};
    tile.onerror=()=>{if(current!==epoch)return;failed++;state.textContent='天地图瓦片加载失败 '+loaded+'/'+total+'，请检查本机转发、Key、配额或网络';};
    tile.src=cfg.tilePath+'/'+layer+'/'+z+'/'+y+'/'+((x%n+n)%n);tiles.appendChild(tile);
   }
  }
 }
 zoomIn.onclick=()=>{z=clamp(z+1);draw();};zoomOut.onclick=()=>{z=clamp(z-1);draw();};
 reset.disabled=false;reset.onclick=()=>{z=cfg.zoom;lon=cfg.lon;lat=cfg.lat;draw();};
 labels.disabled=false;labels.onchange=draw;
 view.onpointerdown=e=>{drag=[e.clientX,e.clientY,...xy()];view.setPointerCapture(e.pointerId);};
 view.onpointerup=e=>{if(drag){move(drag[2]-(e.clientX-drag[0])/256,drag[3]-(e.clientY-drag[1])/256);drag=null;draw();}};
 view.onpointercancel=()=>drag=null;
 view.onwheel=e=>{e.preventDefault();const next=clamp(z+(e.deltaY<0?1:-1));if(next!==z){z=next;draw();}};window.onresize=draw;draw();
}
</script></html>";
        }
    }
}
