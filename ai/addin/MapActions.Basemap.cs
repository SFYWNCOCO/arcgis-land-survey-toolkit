using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace ProAiBridge
{
    // 底图六类（天地图影像/矢量/地形晕渲、高德卫星/矢量、高德行政区划区图）与 GCJ-02 偏移校准。
    // 只加载底图/参考图层，不写业务地块；密钥只由 Pro Python 脚本从 DPAPI 配置读取，不进入命令行与日志。
    internal static partial class MapActions
    {
        private static readonly string[] BasemapToolNames =
            { "add_basemap", "remove_basemap", "calibrate_basemap_offset" };

        internal static bool IsBasemapTool(string name) => Array.IndexOf(BasemapToolNames, name) >= 0;

        internal static async Task<JsonNode> ExecuteBasemapToolAsync(string name, JsonObject args, CancellationToken token,
            string expectedProject, string expectedMap)
        {
            switch (name)
            {
                case "add_basemap": return await AddBasemapAsync(args, token, expectedProject, expectedMap);
                case "remove_basemap": return await RemoveBasemapAsync(args, token, expectedProject, expectedMap);
                case "calibrate_basemap_offset": return await CalibrateBasemapOffsetAsync(args, token, expectedProject, expectedMap);
                default: throw new NotImplementedException("底图工具尚未接入：" + name);
            }
        }

        private static async Task<JsonNode> AddBasemapAsync(JsonObject args, CancellationToken token,
            string expectedProject, string expectedMap)
        {
            var source = ToolProtocol.String(args, "source");
            if (Array.IndexOf(BasemapPreview.Sources, source) < 0)
                throw new ArgumentException("不支持的底图来源：" + source + "。");
            var mode = args["mode"] == null ? "raster" : ToolProtocol.String(args, "mode");
            if (!string.Equals(mode, "raster", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(mode, "live", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("mode 只支持 raster 或 live。");
            var labels = args["labels"] == null || (bool)args["labels"];
            var level = args["level"] == null ? "" : ToolProtocol.String(args, "level").Trim();
            var keywords = args["keywords"] == null ? "" : ToolProtocol.String(args, "keywords").Trim();
            if (source != "amap_district" && (level.Length > 0 || keywords.Length > 0))
                throw new ArgumentException("level/keywords 只对 amap_district 生效；当前来源 " + source +
                    " 请去掉这两个参数（天地图/高德瓦片快照按当前地图范围生成）。");
            if (string.Equals(mode, "live", StringComparison.OrdinalIgnoreCase) && !BasemapPreview.IsTianditu(source))
                throw new ArgumentException("只有天地图底图支持 live 会话底图；高德与区图请用 raster/矢量图层加载。");
            if (source == "amap_district")
            {
                level = level.Length == 0 ? "县乡" : level;
                if (level != "县" && level != "乡" && level != "县乡")
                    throw new ArgumentException("level 只支持“县”“乡”“县乡”。");
                return await AddDistrictAsync(labels, level, keywords, token, expectedProject, expectedMap);
            }
            if (string.Equals(mode, "live", StringComparison.OrdinalIgnoreCase))
                return await AddLiveTiandituAsync(source, labels, token, expectedProject, expectedMap);
            return await AddRasterAsync(source, labels, token, expectedProject, expectedMap);
        }

        private static async Task<JsonNode> AddRasterAsync(string source, bool labels, CancellationToken token,
            string expectedProject, string expectedMap)
        {
            var target = await SnapshotTargetAsync(expectedProject, expectedMap, "add_basemap");
            var settings = BasemapPreview.Load();
            if (BasemapPreview.IsTianditu(source) && string.IsNullOrWhiteSpace(settings.TiandituKey))
                throw new ArgumentException("请在底图设置填写天地图 Key，再加载天地图底图。");
            // 天地图影像/矢量/地形按 labels 叠加注记；高德瓦片与区图没有注记图层，labels 一律忽略。
            var labelsApplied = BasemapPreview.IsTianditu(source) && labels;
            var ignored = labels && !BasemapPreview.IsTianditu(source) ? new[] { "labels" } : Array.Empty<string>();
            // 矢量注记由 Python 快照脚本按 labels 决定是否叠加；C# 无法撤掉已烘进 TIFF 的注记。
            var labelsNote = source == "tianditu_vector"
                ? (labels ? "天地图矢量注记已由快照脚本叠加。" : "labels=false：本次快照未叠加 cva 注记。")
                : null;
            var snapshot = await BasemapSnapshot.CreateAsync(target.Wkid, target.Extent, labelsApplied, source, token);
            token.ThrowIfCancellationRequested();
            return await QueuedTask.Run(() => {
                token.ThrowIfCancellationRequested();
                var map = target.Map;
                var mapKey = BasemapMapKey(map);
                RemoveSessionBasemap(map, mapKey);
                var layer = LayerFactory.Instance.CreateLayer(new Uri(snapshot.Output), map, 0, BasemapLayerName(source, false))
                    ?? throw new InvalidOperationException("本地快照不能加载为图层。");
                TrackBasemapLayer(mapKey, layer, false);
                map.MoveLayer(layer, map.Layers.Count - 1);
                return Node(new
                {
                    added = true, mode = "raster", source, layer = layer.Name, path = snapshot.Output,
                    labels = labelsApplied, labels_requested = labels, labels_applied = labelsApplied, ignored,
                    labels_note = labelsNote,
                    zoom = snapshot.Zoom, coverage = snapshot.Coverage, output_pixel_size_m = snapshot.PixelSize,
                    warning = snapshot.Warning, cache_hit = snapshot.Cached,
                    coordinate_system = BasemapPreview.IsTianditu(source)
                        ? (target.Wkid == 3857
                            ? "源 Web Mercator；地图为地理坐标系，本地快照按 Web Mercator 回退"
                            : "源 Web Mercator；本地快照按当前地图坐标系（WKID " + target.Wkid.ToString(CultureInfo.InvariantCulture) + "）")
                        : "GCJ-02（逐像素纠偏后按当前地图坐标系 WKID " + target.Wkid.ToString(CultureInfo.InvariantCulture) + "）",
                    correction = BasemapPreview.CorrectionLabel(source),
                    legal_status = BasemapPreview.IsGcj02(source) ? "REFERENCE_ONLY" : null,
                    positional_accuracy_status = "UNVERIFIED",
                    note = BasemapPreview.IsGcj02(source)
                        ? "高德瓦片已按公开 GCJ-02 近似算法逐像素纠偏后生成本地快照；仅作参考底图，位置精度未核实，正式成果不得单独依赖。"
                        : "本地快照按当前地图范围生成，保存在 %LOCALAPPDATA%/ProAiBridge/basemap_snapshots，可随工程保存。"
                });
            });
        }

        private static async Task<JsonNode> AddLiveTiandituAsync(string source, bool labels, CancellationToken token,
            string expectedProject, string expectedMap)
        {
            var map = await Ui(() => {
                CheckContext(expectedProject, expectedMap, "add_basemap");
                return MapView.Active?.Map ?? throw new InvalidOperationException("请先激活地图，再加载天地图。");
            });
            return await QueuedTask.Run(() => {
                token.ThrowIfCancellationRequested();
                var settings = BasemapPreview.Load();
                if (string.IsNullOrWhiteSpace(settings.TiandituKey)) throw new ArgumentException("请在底图设置填写天地图 Key。");
                var mapKey = BasemapMapKey(map);
                RemoveSessionBasemap(map, mapKey);
                var proxy = BasemapPreview.StartMap(mapKey, source, settings);
                try
                {
                    var group = LayerFactory.Instance.CreateGroupLayer(map, 0, BasemapSessionGroupName(source));
                    TrackBasemapLayer(mapKey, group, true);
                    var baseLayer = LayerFactory.Instance.CreateLayer(new Uri(proxy.MapTemplate), group, 0,
                        BasemapSessionLayerName(source));
                    Layer labelLayer = null;
                    if (labels) labelLayer = LayerFactory.Instance.CreateLayer(new Uri(proxy.LabelTemplate), group, 0, "天地图注记");
                    map.MoveLayer(group, map.Layers.Count - 1);
                    return Node(new
                    {
                        added = true, mode = "live", source, group = group.Name,
                        labels, labels_requested = labels, labels_applied = labels, ignored = Array.Empty<string>(),
                        session_only = true,
                        layers = new[] { baseLayer?.Name, labelLayer?.Name }.Where(value => value != null).ToArray(),
                        coordinate_system = "CGCS2000 / Web Mercator（在线会话底图）", correction = "NONE",
                        positional_accuracy_status = "UNVERIFIED",
                        note = "会话底图由本机代理转发，随地图/工程关闭移除；可用 remove_basemap 清理。"
                    });
                }
                catch
                {
                    RemoveSessionBasemap(map, mapKey);
                    proxy.Dispose();
                    BasemapPreview.CloseMap(mapKey);
                    throw;
                }
            });
        }

        private static async Task<JsonNode> AddDistrictAsync(bool labels, string level, string keywords,
            CancellationToken token, string expectedProject, string expectedMap)
        {
            string poiKey;
            try { poiKey = AcceptanceAddin.ServiceCredentials.LoadAmapPoi(); }
            catch (Exception ex) { throw new InvalidOperationException("高德 Web 服务 Key 读取失败，请重新填写并保存。", ex); }
            if (string.IsNullOrWhiteSpace(poiKey))
                throw new InvalidOperationException("区图需要高德 Web 服务 Key：请先在采样助手填写高德 POI Key 并保存。");
            var view = await Ui(() => {
                CheckContext(expectedProject, expectedMap, "add_basemap");
                return MapView.Active ?? throw new InvalidOperationException("请先激活地图，再加载区图。");
            });
            // district.py 只要 WGS84 经纬度范围，输出坐标系由 --wkid 指定，取地图坐标系。
            var requested = await QueuedTask.Run(() => {
                var spatialReference = view.Map.SpatialReference
                    ?? throw new InvalidOperationException("地图坐标系未知，不能生成区图。");
                var extent = view.Extent ?? throw new InvalidOperationException("地图范围尚未就绪。");
                return new { Wkid = spatialReference.Wkid > 0 ? spatialReference.Wkid : 3857,
                    Extent = ProjectToWgs84(extent) };
            });
            var project = await Ui(() => Project.Current?.URI ?? "");
            var district = await BasemapDistrict.CreateAsync(project, requested.Wkid, requested.Extent,
                level, keywords, token);
            string[] layers;
            try
            {
                layers = await LoadDistrictLayersAsync(view.Map, district, token);
            }
            catch when (district.Cached)
            {
                // 缓存行可能已失效（GDB 内要素类被清理/移动、界线为空）：忽略缓存重建一次再加载。
                district = await BasemapDistrict.CreateAsync(project, requested.Wkid, requested.Extent,
                    level, keywords, token, ignoreCache: true);
                layers = await LoadDistrictLayersAsync(view.Map, district, token);
            }
            token.ThrowIfCancellationRequested();
            var result = new JsonObject();
            if (district.Receipt != null)
                foreach (var pair in district.Receipt) result[pair.Key] = ToolProtocol.Copy(pair.Value);
            result["added"] = true;
            result["mode"] = "vector_layers";
            result["source"] = "amap_district";
            result["level"] = level;
            result["keywords"] = keywords.Length == 0 ? null : keywords;
            result["labels"] = false;
            result["labels_requested"] = labels;
            result["labels_applied"] = false;
            result["ignored"] = labels
                ? new JsonArray(new[] { (JsonNode)JsonValue.Create("labels") })
                : new JsonArray();
            result["layers"] = new JsonArray(layers.Select(name => (JsonNode)JsonValue.Create(name)).ToArray());
            result["cache_hit"] = district.Cached;
            result["coordinate_system"] = "GCJ-02（参考界线）";
            result["correction"] = "NONE";
            result["positional_accuracy_status"] = "UNVERIFIED";
            if (string.IsNullOrWhiteSpace(result["legal_status"]?.ToString())) result["legal_status"] = "REFERENCE_ONLY";
            var caution = "高德行政区划界线为 GCJ-02 参考数据，不是 CGCS2000 官方界线；正式报件须使用主管部门提供的界线。";
            var scriptNote = result["note"]?.ToString();
            result["note"] = string.IsNullOrWhiteSpace(scriptNote) ? caution : caution + " " + scriptNote;
            return result;
        }

        private static async Task<string[]> LoadDistrictLayersAsync(Map map, BasemapDistrict.Result district,
            CancellationToken token)
        {
            return await QueuedTask.Run(() => {
                token.ThrowIfCancellationRequested();
                var mapKey = BasemapMapKey(map);
                RemoveSessionBasemap(map, mapKey);
                Layer boundary = null, labelLayer = null;
                try
                {
                    boundary = LayerFactory.Instance.CreateLayer(new Uri(district.BoundaryFc), map, 0, "区图-行政界线（高德参考）")
                        ?? throw new InvalidOperationException("区图界线不能加载为图层。");
                    if (!string.IsNullOrWhiteSpace(district.LabelFc))
                        labelLayer = LayerFactory.Instance.CreateLayer(new Uri(district.LabelFc), map, 0, "区图-驻地（高德参考）");
                }
                catch
                {
                    if (labelLayer != null) try { map.RemoveLayer(labelLayer); } catch (Exception) { }
                    if (boundary != null) try { map.RemoveLayer(boundary); } catch (Exception) { }
                    throw;
                }
                TrackBasemapLayer(mapKey, boundary, false);
                if (labelLayer != null) TrackBasemapLayer(mapKey, labelLayer, false);
                if (labelLayer != null) map.MoveLayer(labelLayer, map.Layers.Count - 1);
                map.MoveLayer(boundary, map.Layers.Count - 1);
                return new[] { boundary.Name, labelLayer?.Name }.Where(name => name != null).ToArray();
            });
        }

        private static async Task<JsonNode> RemoveBasemapAsync(JsonObject args, CancellationToken token,
            string expectedProject, string expectedMap)
        {
            var requested = args["layer"] == null ? "" : ToolProtocol.String(args, "layer").Trim();
            return await QueuedTask.Run(() => {
                token.ThrowIfCancellationRequested();
                var map = MapView.Active?.Map ?? throw new InvalidOperationException("请先激活地图，再移除底图。");
                var mapKey = BasemapMapKey(map);
                if (!OwnedBasemapUris.TryGetValue(mapKey, out var owned) || owned.IsEmpty)
                    return Node(new { removed = 0, layers = Array.Empty<string>(),
                        note = "当前地图没有本助手加载的底图图层。" });
                if (requested.Length == 0)
                {
                    var tracked = map.GetLayersAsFlattenedList()
                        .Where(layer => owned.ContainsKey(layer.URI)).Select(layer => layer.Name).ToArray();
                    var removed = RemoveSessionBasemap(map, mapKey);
                    return Node(new { removed = removed ? tracked.Length : 0, layers = tracked });
                }
                var candidates = map.GetLayersAsFlattenedList().Where(layer => owned.ContainsKey(layer.URI)).ToArray();
                var match = candidates.FirstOrDefault(layer => layer.URI == requested || layer.Name == requested);
                if (match == null)
                    throw new ArgumentException("该图层不是本助手加载的底图，未移除（不改业务图层）。");
                map.RemoveLayer(match);
                owned.TryRemove(match.URI, out _);
                if (owned.IsEmpty)
                {
                    OwnedBasemapUris.TryRemove(mapKey, out _);
                    // 本机瓦片代理只为 live 会话底图存在；最后一个受管图层移除后不应继续监听。
                    BasemapPreview.CloseMap(mapKey);
                }
                return Node(new { removed = 1, layers = new[] { match.Name } });
            });
        }

        private static async Task<JsonNode> CalibrateBasemapOffsetAsync(JsonObject args, CancellationToken token,
            string expectedProject, string expectedMap)
        {
            var source = args["source"] == null ? "amap_satellite" : ToolProtocol.String(args, "source");
            if (!BasemapPreview.IsGcj02(source))
                throw new ArgumentException("偏移校准只针对高德 GCJ-02 来源，天地图无需纠偏。");
            var explicitExtent = ReadOptionalExtent(args["extent"]);
            var view = await Ui(() => {
                CheckContext(expectedProject, expectedMap, "calibrate_basemap_offset");
                return MapView.Active ?? throw new InvalidOperationException("请先激活地图，再校准底图偏移。");
            });
            var prepared = await QueuedTask.Run(() => {
                token.ThrowIfCancellationRequested();
                var spatialReference = view.Map.SpatialReference;
                if (spatialReference == null || spatialReference.IsUnknown)
                    throw new InvalidOperationException("地图坐标系未知，不能把范围换算到 WGS84。");
                Envelope envelope = explicitExtent == null
                    ? view.Extent ?? throw new InvalidOperationException("地图范围尚未就绪。")
                    : EnvelopeBuilderEx.CreateEnvelope(explicitExtent[0], explicitExtent[1], explicitExtent[2], explicitExtent[3], spatialReference);
                var input = explicitExtent ?? new[] { envelope.XMin, envelope.YMin, envelope.XMax, envelope.YMax };
                var name = string.IsNullOrWhiteSpace(spatialReference.Name) ? "地图坐标系" : spatialReference.Name;
                return new
                {
                    Wkid = spatialReference.Wkid,
                    Crs = name + "（WKID " + spatialReference.Wkid.ToString(CultureInfo.InvariantCulture) + "）",
                    Input = input,
                    Projected = spatialReference.IsProjected,
                    Wgs84 = ProjectToWgs84(envelope)
                };
            });
            var extentWgs84 = prepared.Wgs84;
            var script = AcceptanceScriptPath("basemap_offset.py");
            var run = await Task.Run(() => RunScript(script, new[]
            {
                "--extent",
                extentWgs84[0].ToString("R", CultureInfo.InvariantCulture),
                extentWgs84[1].ToString("R", CultureInfo.InvariantCulture),
                extentWgs84[2].ToString("R", CultureInfo.InvariantCulture),
                extentWgs84[3].ToString("R", CultureInfo.InvariantCulture),
            }, 120000, token), token);
            token.ThrowIfCancellationRequested();
            var status = run.Json?["status"]?.ToString();
            if (run.ExitCode != 0 || !string.Equals(status, "OK", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("底图偏移计算失败：" + ScriptReason(run, "请检查网络与 ArcGIS Pro Python 环境。"));
            var scriptNote = run.Json?["note"]?.ToString();
            var result = new JsonObject();
            if (run.Json != null)
                foreach (var pair in run.Json) result[pair.Key] = ToolProtocol.Copy(pair.Value);
            result["source"] = source;
            result["extent_input"] = new JsonArray(prepared.Input
                .Select(value => (JsonNode)JsonValue.Create(Math.Round(value, 6))).ToArray());
            result["extent_input_crs"] = prepared.Crs;
            result["extent_wgs84"] = new JsonArray(extentWgs84
                .Select(value => (JsonNode)JsonValue.Create(Math.Round(value, 8))).ToArray());
            result["units"] = "extent 输入按当前地图坐标系（" + prepared.Crs + "）的 xmin/ymin/xmax/ymax；" +
                "extent_wgs84 为 WGS84 经纬度（单位：度）。省略 extent 时取当前地图视图范围。";
            result["positional_accuracy_status"] = "UNVERIFIED";
            var disclaimer = "GCJ-02 为公开近似算法，偏移量为估算值；不能替代控制点配准与测绘精度核查。";
            result["note"] = string.IsNullOrWhiteSpace(scriptNote) ? disclaimer : disclaimer + " " + scriptNote;
            // 防御：投影地图却传入像经纬度的范围，多半是单位传错；明确告警，避免静默算出近 0 偏移。
            if (explicitExtent != null && prepared.Projected &&
                Math.Abs(prepared.Input[0]) <= 180 && Math.Abs(prepared.Input[2]) <= 180 &&
                Math.Abs(prepared.Input[1]) <= 90 && Math.Abs(prepared.Input[3]) <= 90)
                result["warning"] = "extent 数值看起来像 WGS84 经纬度，但当前地图是投影坐标系，已按地图坐标系解释；" +
                    "请确认单位是否一致，或省略 extent 使用当前地图范围。";
            return result;
        }

        // 把任意地图坐标系范围的四角换算成 WGS84 经纬度（底图 Python 脚本统一按 WGS84 取瓦片/反查行政区划）。
        private static double[] ProjectToWgs84(Envelope envelope)
        {
            if (envelope?.SpatialReference == null || envelope.SpatialReference.IsUnknown)
                throw new InvalidOperationException("范围坐标系未知，不能换算到 WGS84。");
            var spatialReference = envelope.SpatialReference;
            double xmin, ymin, xmax, ymax;
            if (spatialReference.IsGeographic && spatialReference.Wkid == 4326)
            {
                xmin = envelope.XMin; ymin = envelope.YMin; xmax = envelope.XMax; ymax = envelope.YMax;
            }
            else
            {
                var corners = new[] {
                    MapPointBuilderEx.CreateMapPoint(envelope.XMin, envelope.YMin, spatialReference),
                    MapPointBuilderEx.CreateMapPoint(envelope.XMax, envelope.YMin, spatialReference),
                    MapPointBuilderEx.CreateMapPoint(envelope.XMin, envelope.YMax, spatialReference),
                    MapPointBuilderEx.CreateMapPoint(envelope.XMax, envelope.YMax, spatialReference) };
                var projected = corners
                    .Select(point => (MapPoint)GeometryEngine.Instance.Project(point, SpatialReferences.WGS84))
                    .ToArray();
                xmin = projected.Min(point => point.X);
                ymin = projected.Min(point => point.Y);
                xmax = projected.Max(point => point.X);
                ymax = projected.Max(point => point.Y);
            }
            if (xmin < -180 || xmax > 180 || ymin < -85 || ymax > 85)
                throw new InvalidOperationException("换算后的 WGS84 范围超出高德服务覆盖（经度±180、纬度±85）。");
            return new[] { xmin, ymin, xmax, ymax };
        }

        private static async Task<SnapshotTarget> SnapshotTargetAsync(string expectedProject, string expectedMap, string name)
        {
            var view = await Ui(() => {
                CheckContext(expectedProject, expectedMap, name);
                return MapView.Active ?? throw new InvalidOperationException("请先激活地图，再加载底图。");
            });
            return await QueuedTask.Run(() => {
                var spatialReference = view.Map.SpatialReference
                    ?? throw new InvalidOperationException("地图坐标系未知，不能生成底图快照。");
                var extent = view.Extent ?? throw new InvalidOperationException("地图范围尚未就绪。");
                if (spatialReference.IsProjected && spatialReference.Wkid > 0)
                    return new SnapshotTarget(view.Map, spatialReference.Wkid,
                        new[] { extent.XMin, extent.YMin, extent.XMax, extent.YMax });
                var p1 = (MapPoint)GeometryEngine.Instance.Project(
                    MapPointBuilderEx.CreateMapPoint(extent.XMin, extent.YMin, spatialReference), SpatialReferences.WebMercator);
                var p2 = (MapPoint)GeometryEngine.Instance.Project(
                    MapPointBuilderEx.CreateMapPoint(extent.XMax, extent.YMax, spatialReference), SpatialReferences.WebMercator);
                return new SnapshotTarget(view.Map, 3857, new[] { p1.X, p1.Y, p2.X, p2.Y });
            });
        }

        private static string BasemapMapKey(Map map) => (Project.Current?.URI ?? "") + "|" + map.URI;

        private static string BasemapLayerName(string source, bool live) => source switch {
            "tianditu_image" => live ? "天地图影像（会话底图）" : "天地图影像（本地快照）",
            "tianditu_vector" => live ? "天地图矢量（会话底图）" : "天地图矢量（本地快照）",
            "tianditu_terrain" => live ? "天地图地形（会话底图）" : "天地图地形（本地快照）",
            "amap_satellite" => "高德卫星（GCJ-02纠偏快照）",
            "amap_vector" => "高德矢量（GCJ-02纠偏快照）",
            _ => throw new ArgumentException("不支持的底图来源：" + source) };

        private static string BasemapSessionGroupName(string source) => source switch {
            "tianditu_image" => "天地图影像（会话底图）",
            "tianditu_vector" => "天地图矢量（会话底图）",
            "tianditu_terrain" => "天地图地形（会话底图）",
            _ => throw new ArgumentException("不支持的会话底图来源：" + source) };

        private static string BasemapSessionLayerName(string source) => source switch {
            "tianditu_image" => "天地图影像",
            "tianditu_vector" => "天地图矢量",
            "tianditu_terrain" => "天地图地形晕渲",
            _ => throw new ArgumentException("不支持的会话底图来源：" + source) };

        private static string BasemapKeyHash(string source)
        {
            string value;
            if (BasemapPreview.IsTianditu(source))
            {
                value = BasemapPreview.Load().TiandituKey ?? "";
            }
            else if (source == "amap_district")
            {
                value = AcceptanceAddin.ServiceCredentials.LoadAmapPoi() ?? "";
            }
            else
            {
                value = BasemapPreview.Load().AmapKey ?? "";
            }
            return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).Substring(0, 8);
        }

        private static double[] ReadOptionalExtent(JsonNode node)
        {
            if (node == null) return null;
            if (node is not JsonArray array || array.Count != 4)
                throw new ArgumentException("extent 须为 [xmin, ymin, xmax, ymax] 四个数。");
            var values = new double[4];
            for (var index = 0; index < 4; index++)
            {
                if (!double.TryParse(array[index]?.ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out values[index]) ||
                    !double.IsFinite(values[index]))
                    throw new ArgumentException("extent 须为 [xmin, ymin, xmax, ymax] 四个有限数。");
            }
            if (values[0] >= values[2] || values[1] >= values[3])
                throw new ArgumentException("extent 须满足 xmin < xmax 且 ymin < ymax。");
            return values;
        }

        private static string AcceptanceScriptPath(string name)
        {
            var assemblyDir = Path.GetDirectoryName(typeof(MapActions).Assembly.Location) ?? "";
            var script = Path.Combine(assemblyDir, "acceptance", name);
            if (!File.Exists(script)) throw new FileNotFoundException("插件未包含底图脚本：" + name + "（" + script + "）");
            return script;
        }

        private static string ProPythonExecutable()
        {
            var coreDir = Path.GetDirectoryName(typeof(Project).Assembly.Location) ?? "";
            var python = Path.GetFullPath(Path.Combine(coreDir, "..", "..", "Python", "envs", "arcgispro-py3", "python.exe"));
            if (!File.Exists(python)) throw new FileNotFoundException("找不到 ArcGIS Pro Python：" + python);
            return python;
        }

        private sealed class ScriptResult
        {
            internal JsonObject Json { get; set; }
            internal string Stderr { get; set; }
            internal int ExitCode { get; set; }
        }

        // 同步执行脚本；调用方必须放在 Task.Run 内，避免阻塞地理处理线程。
        private static ScriptResult RunScript(string script, IReadOnlyList<string> arguments, int timeoutMs, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            var info = new ProcessStartInfo(ProPythonExecutable())
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                // Pro 是 GUI 进程，默认 Console.OutputEncoding 可能不是 UTF-8；脚本固定输出 UTF-8，
                // 这里必须显式按 UTF-8 解码，否则中文会被按本地代码页误解、甚至吞掉 JSON 引号导致解析失败。
                StandardOutputEncoding = new UTF8Encoding(false),
                StandardErrorEncoding = new UTF8Encoding(false)
            };
            // 子进程固定 UTF-8 输出，不依赖父进程是否设置了 PYTHONIOENCODING/PYTHONUTF8。
            info.Environment["PYTHONIOENCODING"] = "utf-8";
            info.Environment["PYTHONUTF8"] = "1";
            // 脚本以 <assembly>/acceptance/*.py 直接运行；basemap_snapshot.py 等按 acceptance 包相互引用，
            // 因此把程序集目录（acceptance 的父目录）加入工作目录与 PYTHONPATH，避免 ImportError。
            var assemblyDir = Path.GetDirectoryName(typeof(MapActions).Assembly.Location) ?? "";
            if (assemblyDir.Length > 0)
            {
                info.WorkingDirectory = assemblyDir;
                var pythonPath = Environment.GetEnvironmentVariable("PYTHONPATH");
                info.Environment["PYTHONPATH"] = string.IsNullOrWhiteSpace(pythonPath)
                    ? assemblyDir : assemblyDir + Path.PathSeparator + pythonPath;
            }
            info.ArgumentList.Add(script);
            foreach (var value in arguments) info.ArgumentList.Add(value);
            using var process = Process.Start(info) ?? throw new InvalidOperationException("无法启动底图脚本进程。");
            using var registration = token.Register(() => { try { process.Kill(true); } catch (Exception) { } });
            var stdout = process.StandardOutput.ReadToEndAsync();
            var stderr = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(timeoutMs))
            {
                try { process.Kill(true); } catch (Exception) { }
                process.WaitForExit(5000);
                throw new TimeoutException("底图脚本运行超过 " + (timeoutMs / 1000).ToString(CultureInfo.InvariantCulture) + " 秒。");
            }
            var text = stdout.GetAwaiter().GetResult().Trim();
            var error = stderr.GetAwaiter().GetResult().Trim();
            token.ThrowIfCancellationRequested();
            var line = text.Split('\n').LastOrDefault(value => value.TrimStart().StartsWith("{", StringComparison.Ordinal));
            JsonObject parsed = null;
            try { parsed = line == null ? null : JsonNode.Parse(line.Trim()) as JsonObject; }
            catch (JsonException) { }
            return new ScriptResult { Json = parsed, Stderr = error, ExitCode = process.ExitCode };
        }

        private static string ScriptReason(ScriptResult run, string fallback)
        {
            var reason = run.Json?["error"]?.ToString() ?? run.Json?["warning"]?.ToString() ?? run.Json?["note"]?.ToString();
            if (string.IsNullOrWhiteSpace(reason)) reason = run.Stderr;
            if (string.IsNullOrWhiteSpace(reason)) reason = fallback;
            return AcceptanceAddin.WorkbenchDiagnostics.Redact(reason.Trim());
        }

        private static void DeleteFolderOrFile(string root, string target)
        {
            var safeRoot = Path.GetFullPath(root) + Path.DirectorySeparatorChar;
            var safeTarget = Path.GetFullPath(target);
            if (!safeTarget.StartsWith(safeRoot, StringComparison.OrdinalIgnoreCase)) return;
            try
            {
                if (Directory.Exists(safeTarget)) Directory.Delete(safeTarget, true);
                else if (File.Exists(safeTarget)) File.Delete(safeTarget);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        private static int? JsonInt(JsonNode node) =>
            int.TryParse(node?.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value) ? value : (int?)null;

        private static class BasemapSnapshot
        {
            internal sealed class Result
            {
                internal string Output { get; set; }
                internal int? Zoom { get; set; }
                internal string Coverage { get; set; }
                internal string PixelSize { get; set; }
                internal string Warning { get; set; }
                internal bool Cached { get; set; }
            }

            internal static async Task<Result> CreateAsync(int wkid, double[] extent, bool labels, string source,
                CancellationToken token) =>
                await Task.Run(() => Create(wkid, extent, labels, source, token), token);

            private static Result Create(int wkid, double[] extent, bool labels, string source, CancellationToken token)
            {
                var root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "ProAiBridge", "basemap_snapshots");
                Directory.CreateDirectory(root);
                var cacheFile = Path.Combine(root, "cache.json");
                var cacheKey = BasemapPreview.SnapshotCacheKey(wkid, extent, BasemapKeyHash(source) + "|" + source,
                    labels && BasemapPreview.IsTianditu(source));
                var cached = ReadCache(cacheFile, cacheKey);
                if (cached != null) return cached;
                var script = AcceptanceScriptPath("basemap_snapshot.py");
                var folder = Path.Combine(root, "snapshot_" + source + "_" +
                    DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture) + "_" +
                    Guid.NewGuid().ToString("N").Substring(0, 6));
                Directory.CreateDirectory(folder);
                var output = Path.Combine(folder, source + "_snapshot.tif");
                try
                {
                    var run = RunScript(script, new[]
                    {
                        "--out", output,
                        "--wkid", wkid.ToString(CultureInfo.InvariantCulture),
                        "--extent",
                        extent[0].ToString("R", CultureInfo.InvariantCulture),
                        extent[1].ToString("R", CultureInfo.InvariantCulture),
                        extent[2].ToString("R", CultureInfo.InvariantCulture),
                        extent[3].ToString("R", CultureInfo.InvariantCulture),
                        "--source", source,
                        "--labels", labels && BasemapPreview.IsTianditu(source) ? "true" : "false",
                        "--max-tiles", "200",
                        "--max-zoom", "18"
                    }, 120000, token);
                    token.ThrowIfCancellationRequested();
                    var ok = run.Json?["ok"] is JsonValue okValue && okValue.TryGetValue<bool>(out var flag) && flag;
                    if (run.ExitCode != 0 || !ok)
                        throw new InvalidOperationException((BasemapPreview.IsGcj02(source) ? "高德纠偏快照未生成：" : "天地图快照未生成：") +
                            ScriptReason(run, BasemapPreview.IsGcj02(source)
                                ? "请检查网络与高德瓦片服务可用性。"
                                : "请检查网络与天地图 Key、配额。"));
                    var path = run.Json?["output"]?.ToString();
                    if (string.IsNullOrWhiteSpace(path)) path = output;
                    if (!File.Exists(path)) throw new InvalidOperationException("底图快照未生成有效 TIFF。");
                    var result = new Result
                    {
                        Output = Path.GetFullPath(path),
                        Zoom = JsonInt(run.Json?["zoom"]),
                        Coverage = run.Json?["coverage_status"]?.ToString(),
                        PixelSize = run.Json?["output_pixel_size_m"]?.ToString(),
                        Warning = run.Json?["warning"]?.ToString(),
                        Cached = false
                    };
                    WriteCache(cacheFile, cacheKey, result);
                    return result;
                }
                catch
                {
                    DeleteFolderOrFile(root, folder);
                    throw;
                }
            }

            private static Result ReadCache(string file, string key)
            {
                try
                {
                    if (!File.Exists(file)) return null;
                    if (JsonNode.Parse(File.ReadAllText(file)) is not JsonObject root) return null;
                    if (root[key] is not JsonObject row) return null;
                    var cachedOutput = row["output"]?.ToString();
                    if (string.IsNullOrWhiteSpace(cachedOutput) || !File.Exists(cachedOutput)) return null;
                    return new Result
                    {
                        Output = cachedOutput,
                        Zoom = JsonInt(row["zoom"]),
                        Coverage = row["coverage"]?.ToString(),
                        PixelSize = row["pixel_size"]?.ToString(),
                        Warning = row["warning"]?.ToString(),
                        Cached = true
                    };
                }
                catch (IOException) { return null; }
                catch (JsonException) { return null; }
            }

            private static void WriteCache(string file, string key, Result result)
            {
                try
                {
                    var root = (File.Exists(file) ? JsonNode.Parse(File.ReadAllText(file)) as JsonObject : null) ?? new JsonObject();
                    root[key] = new JsonObject
                    {
                        ["output"] = result.Output, ["zoom"] = result.Zoom, ["coverage"] = result.Coverage,
                        ["pixel_size"] = result.PixelSize, ["warning"] = result.Warning,
                        ["created"] = DateTimeOffset.UtcNow.ToString("O")
                    };
                    if (root.Count > 20)
                    {
                        var trimmed = new JsonObject();
                        foreach (var pair in root.OrderByDescending(item => (string)item.Value?["created"] ?? "").Take(20))
                            trimmed[pair.Key] = pair.Value == null ? null : ToolProtocol.Copy(pair.Value);
                        root = trimmed;
                    }
                    var temp = file + "." + Guid.NewGuid().ToString("N") + ".tmp";
                    File.WriteAllText(temp, root.ToJsonString());
                    File.Move(temp, file, true);
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                catch (JsonException) { }
            }
        }

        private static class BasemapDistrict
        {
            internal sealed class Result
            {
                internal string Gdb { get; set; }
                internal string BoundaryFc { get; set; }
                internal string LabelFc { get; set; }
                internal int FeatureCount { get; set; }
                internal JsonObject Receipt { get; set; }
                internal bool Cached { get; set; }
            }

            internal static async Task<Result> CreateAsync(string project, int wkid, double[] extentWgs84,
                string level, string keywords, CancellationToken token, bool ignoreCache = false) =>
                await Task.Run(() => Create(project, wkid, extentWgs84, level, keywords, token, ignoreCache), token);

            private static Result Create(string project, int wkid, double[] extentWgs84, string level, string keywords,
                CancellationToken token, bool ignoreCache)
            {
                var root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "ProAiBridge", "district");
                Directory.CreateDirectory(root);
                var projectHash = Convert.ToHexString(SHA256.HashData(
                    Encoding.UTF8.GetBytes(project ?? ""))).Substring(0, 12).ToLowerInvariant();
                var gdb = Path.Combine(root, projectHash + ".gdb");
                var cacheFile = Path.Combine(root, "cache.json");
                // 区图范围是 WGS84 度：用 1e-6 度级精度，避免 0.1°（≈11 km）内平移视图命中旧范围；
                // level/keywords 会改变成果内容，必须进入缓存键。
                var cacheKey = BasemapPreview.SnapshotCacheKey(wkid, extentWgs84,
                    BasemapKeyHash("amap_district") + "|amap_district|" + level + "|" + keywords, false,
                    BasemapPreview.DistrictExtentDecimals);
                var cached = ignoreCache ? null : ReadCache(cacheFile, cacheKey, gdb);
                if (cached != null) return cached;
                var script = AcceptanceScriptPath("district.py");
                // 要素类基名带一次性后缀：缓存被裁剪/失效后重跑同一范围也不会撞“要素类已存在”，
                // 同时保持 GDB 路径按工程固定，方便随工程复用。
                var name = "district_" + Convert.ToHexString(SHA256.HashData(
                    Encoding.UTF8.GetBytes(cacheKey))).Substring(0, 8).ToLowerInvariant() + "_" +
                    Guid.NewGuid().ToString("N").Substring(0, 6);
                var arguments = new List<string>
                {
                    "--out-gdb", gdb,
                    "--name", name,
                    "--wkid", wkid.ToString(CultureInfo.InvariantCulture),
                    "--extent",
                    extentWgs84[0].ToString("R", CultureInfo.InvariantCulture),
                    extentWgs84[1].ToString("R", CultureInfo.InvariantCulture),
                    extentWgs84[2].ToString("R", CultureInfo.InvariantCulture),
                    extentWgs84[3].ToString("R", CultureInfo.InvariantCulture),
                    "--subdistrict", "2",
                    "--level", level
                };
                if (keywords.Length > 0) { arguments.Add("--keywords"); arguments.Add(keywords); }
                var run = RunScript(script, arguments, 120000, token);
                token.ThrowIfCancellationRequested();
                var status = run.Json?["status"]?.ToString();
                if (run.ExitCode != 0 || !string.Equals(status, "OK", StringComparison.OrdinalIgnoreCase))
                {
                    // district.py 无界线时以 NO_BOUNDARY 文本失败（status=FAIL）；仍需给出可操作中文提示。
                    var failureText = (run.Json?["error"]?.ToString() ?? "") + " " + (run.Json?["note"]?.ToString() ?? "");
                    var noBoundary = string.Equals(status, "NO_BOUNDARY", StringComparison.OrdinalIgnoreCase) ||
                        failureText.Contains("NO_BOUNDARY", StringComparison.OrdinalIgnoreCase);
                    var reason = ScriptReason(run, "请检查高德 Web 服务 Key、配额与网络。");
                    throw new InvalidOperationException("行政区划区图未生成：" + (noBoundary
                        ? "范围内没有可用行政界线（NO_BOUNDARY），请缩小/移动范围，或改用 level/keywords 后重试。"
                        : reason));
                }
                var boundaryFc = run.Json?["boundary_fc"]?.ToString();
                if (string.IsNullOrWhiteSpace(boundaryFc))
                    throw new InvalidOperationException("行政区划区图未返回界线图层（boundary_fc）。");
                var featureCount = JsonInt(run.Json?["feature_count"]) ?? 0;
                if (featureCount <= 0)
                    throw new InvalidOperationException("行政区划区图没有可用界线（feature_count=" +
                        featureCount.ToString(CultureInfo.InvariantCulture) + "），未加载图层；请调整范围、level 或 keywords 后重试。");
                var labelFc = run.Json?["label_fc"]?.ToString();
                var reportedGdb = run.Json?["gdb"]?.ToString();
                var result = new Result
                {
                    Gdb = !string.IsNullOrWhiteSpace(reportedGdb) && Directory.Exists(reportedGdb) ? reportedGdb : gdb,
                    BoundaryFc = boundaryFc,
                    LabelFc = string.IsNullOrWhiteSpace(labelFc) ? null : labelFc,
                    FeatureCount = featureCount,
                    Receipt = run.Json,
                    Cached = false
                };
                if (!Directory.Exists(result.Gdb))
                    throw new InvalidOperationException("行政区划区图脚本未生成有效 GDB：" + Path.GetFileName(gdb));
                WriteCache(cacheFile, cacheKey, result);
                return result;
            }

            private static Result ReadCache(string file, string key, string gdb)
            {
                try
                {
                    if (!File.Exists(file)) return null;
                    if (JsonNode.Parse(File.ReadAllText(file)) is not JsonObject root) return null;
                    if (root[key] is not JsonObject row) return null;
                    var boundary = row["boundary_fc"]?.ToString();
                    if (string.IsNullOrWhiteSpace(boundary) || !Directory.Exists(gdb)) return null;
                    // 旧缓存行没有 feature_count 或界线为空时不命中：让调用方重建，避免加载空层。
                    var featureCount = JsonInt(row["feature_count"]) ?? 0;
                    if (featureCount <= 0) return null;
                    return new Result
                    {
                        Gdb = gdb,
                        BoundaryFc = boundary,
                        LabelFc = row["label_fc"]?.ToString(),
                        FeatureCount = featureCount,
                        Receipt = row["receipt"] as JsonObject,
                        Cached = true
                    };
                }
                catch (IOException) { return null; }
                catch (JsonException) { return null; }
            }

            private static void WriteCache(string file, string key, Result result)
            {
                try
                {
                    var root = (File.Exists(file) ? JsonNode.Parse(File.ReadAllText(file)) as JsonObject : null) ?? new JsonObject();
                    root[key] = new JsonObject
                    {
                        ["gdb"] = result.Gdb, ["boundary_fc"] = result.BoundaryFc, ["label_fc"] = result.LabelFc,
                        ["feature_count"] = result.FeatureCount,
                        ["receipt"] = result.Receipt == null ? null : ToolProtocol.Copy(result.Receipt),
                        ["created"] = DateTimeOffset.UtcNow.ToString("O")
                    };
                    if (root.Count > 20)
                    {
                        var trimmed = new JsonObject();
                        foreach (var pair in root.OrderByDescending(item => (string)item.Value?["created"] ?? "").Take(20))
                            trimmed[pair.Key] = pair.Value == null ? null : ToolProtocol.Copy(pair.Value);
                        root = trimmed;
                    }
                    var temp = file + "." + Guid.NewGuid().ToString("N") + ".tmp";
                    File.WriteAllText(temp, root.ToJsonString());
                    File.Move(temp, file, true);
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                catch (JsonException) { }
            }
        }
    }
}
