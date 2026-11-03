using System;
using System.Collections.Concurrent;
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
using ArcGIS.Core.CIM;
using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Editing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace ProAiBridge
{
    // AI 绘图（点/线/面）：GeoJSON 解析 → 投影到地图坐标系 → 预览 / confirm 写入 / 按批次撤销。
    // 只登记并撤销本进程本会话由助手创建的要素；绝不按条件删除业务要素。
    internal static partial class MapActions
    {
        private static readonly string[] DrawingToolNames =
            { "draw_features", "list_drawings", "clear_drawings" };

        internal static bool IsDrawingTool(string name) => Array.IndexOf(DrawingToolNames, name) >= 0;

        private const int DrawingMaxVertices = 50000;
        private const string DrawingTempLayerName = "AI绘制（临时）";
        private const string DrawingTempTargetName = "临时图形";

        // 撤销基础：key = 工程 URI + "|" + 地图 URI，只保存本进程本会话由助手创建的要素/临时层。
        private static readonly ConcurrentDictionary<string, List<DrawingBatch>> DrawingBatches =
            new ConcurrentDictionary<string, List<DrawingBatch>>(StringComparer.Ordinal);

        private sealed class DrawingBatch
        {
            internal string Id { get; set; }
            internal DateTimeOffset Created { get; set; }
            internal string TargetLayerUri { get; set; }
            internal string TargetKind { get; set; }
            internal List<long> Oids { get; set; } = new List<long>();
            internal string FcPath { get; set; }
            internal string FeatureClassPath { get; set; }
            internal string LayerUri { get; set; }
            internal string GeometryType { get; set; }
            internal int VertexCount { get; set; }
            // P2-6：PENDING=已登记但尚未确认写入；CREATED=写入确认且 OID 已回读；
            // CREATED_NO_OID=写入确认但 OID 未回读；UNKNOWN=写入结果未知（异常/取消），需人工核对。
            internal string State { get; set; }
            internal string Warning { get; set; }
        }

        private sealed class DrawingGeometry
        {
            internal Geometry Geometry { get; set; }
            internal string Crs { get; set; }
            internal double? LengthMeters { get; set; }
            internal double? AreaSquareMeters { get; set; }
            internal string MeasureMethod { get; set; }
            // 创建临时要素类时要传给 GP 的地图坐标系（WKID 或 WKT），在 QueuedTask 内读取，避免在后台线程碰 Map 属性。
            internal string SpatialReferenceParameter { get; set; }
        }

        private sealed class DrawingContext
        {
            internal Map Map { get; set; }
            internal string ProjectUri { get; set; }
            internal string MapUri { get; set; }
        }

        internal static Task<JsonNode> ExecuteDrawingToolAsync(string name, JsonObject args, CancellationToken token,
            string expectedProject, string expectedMap)
        {
            switch (name)
            {
                case "draw_features": return DrawFeaturesAsync(args, token, expectedProject, expectedMap);
                case "list_drawings": return ListDrawingsAsync(token, expectedProject, expectedMap);
                case "clear_drawings": return ClearDrawingsAsync(args, token, expectedProject, expectedMap);
                default: return Task.FromException<JsonNode>(new ArgumentException("尚未实现绘图工具：" + name));
            }
        }

        // ---------- draw_features ----------

        private static async Task<JsonNode> DrawFeaturesAsync(JsonObject args, CancellationToken token,
            string expectedProject, string expectedMap)
        {
            token.ThrowIfCancellationRequested();
            var geometryText = ToolProtocol.String(args, "geometry").Trim();
            if (geometryText.Length == 0) throw new ArgumentException("geometry 不能为空，请提供 GeoJSON 几何对象字符串。");
            var crs = ToolProtocol.String(args, "crs", "WGS84").Trim().ToUpperInvariant();
            if (crs != "WGS84" && crs != "MAP") throw new ArgumentException("crs 仅支持 WGS84 或 MAP。");
            var layerInput = ToolProtocol.String(args, "layer").Trim();
            var attributesText = ToolProtocol.String(args, "attributes").Trim();
            var confirm = args["confirm"] != null && (bool)args["confirm"];
            var context = await Ui(() => {
                CheckContext(expectedProject, expectedMap, "draw_features");
                var active = MapView.Active?.Map ?? throw new InvalidOperationException("没有活动地图。请先打开地图视图。");
                return new DrawingContext { Map = active, ProjectUri = Project.Current?.URI ?? "", MapUri = active.URI };
            });
            var map = context.Map;

            if (!confirm)
            {
                return await QueuedTask.Run(() => {
                    token.ThrowIfCancellationRequested();
                    var prepared = PrepareDrawing(geometryText, crs, map, token);
                    FeatureLayer target = null;
                    if (layerInput.Length > 0) target = ResolveDrawingTarget(map, layerInput, prepared.Geometry.GeometryType, false);
                    return DrawingPreviewResult(prepared, target);
                });
            }

            // P2-13：Map 属性读取与几何构造放在 QueuedTask；临时 GDB/要素类的地理处理放到 UI 线程并显式
            // GPExecuteToolFlags.GPThread（与仓库既有 GP 调用先例一致）；图层创建与要素编辑再回 QueuedTask。
            var preparedDrawing = await QueuedTask.Run(() => {
                token.ThrowIfCancellationRequested();
                if (map.MapType != MapType.Map) throw new NotSupportedException("绘图仅支持二维地图；三维场景请使用 Pro 自带编辑工具。");
                return PrepareDrawing(geometryText, crs, map, token);
            });
            token.ThrowIfCancellationRequested();
            var mapKey = DrawingMapKey(context.ProjectUri, context.MapUri);
            if (layerInput.Length > 0)
                return await QueuedTask.Run(async () => {
                    token.ThrowIfCancellationRequested();
                    var target = ResolveDrawingTarget(map, layerInput, preparedDrawing.Geometry.GeometryType, true);
                    return await CreateDrawingFeatureAsync(mapKey, target, preparedDrawing, attributesText, token);
                });
            return await CreateDrawingTempAsync(map, mapKey, context.ProjectUri, preparedDrawing, attributesText, token);
        }

        private static DrawingGeometry PrepareDrawing(string geometryText, string crs, Map map, CancellationToken token)
        {
            var mapSr = map.SpatialReference;
            if (mapSr == null || mapSr.IsUnknown)
                throw new ArgumentException("地图坐标系未定义，不能绘制几何；请先为地图设置坐标系。");
            var inputSr = crs == "WGS84" ? SpatialReferences.WGS84 : mapSr;
            var geometry = ParseGeoJsonGeometry(geometryText, inputSr, token);
            token.ThrowIfCancellationRequested();
            if (crs == "WGS84")
            {
                Geometry projected;
                try { projected = GeometryEngine.Instance.Project(geometry, mapSr); }
                catch (Exception ex) when (ex is not OperationCanceledException)
                {
                    throw new InvalidOperationException("WGS84 坐标投影到地图坐标系失败：" + ex.Message +
                        "；请核对经纬度范围与地图坐标系。", ex);
                }
                if (projected == null || projected.IsEmpty)
                    throw new InvalidOperationException("几何从 WGS84 转换到地图坐标系失败；请核对坐标范围与地图坐标系。");
                geometry = projected;
            }
            var prepared = new DrawingGeometry { Geometry = geometry, Crs = crs };
            prepared.SpatialReferenceParameter = mapSr.Wkid > 0
                ? mapSr.Wkid.ToString(CultureInfo.InvariantCulture)
                : mapSr.Wkt;
            var unit = mapSr.Unit as LinearUnit;
            if (geometry.GeometryType == GeometryType.Polyline)
            {
                if (mapSr.IsGeographic || unit == null)
                {
                    prepared.LengthMeters = GeometryEngine.Instance.GeodesicLength(geometry, LinearUnit.Meters);
                    prepared.MeasureMethod = "大地线长度（米），按椭球计算";
                }
                else
                {
                    prepared.LengthMeters = GeometryEngine.Instance.Length(geometry) * unit.MetersPerUnit;
                    prepared.MeasureMethod = "平面长度（米），按地图坐标系单位换算";
                }
            }
            else if (geometry.GeometryType == GeometryType.Polygon)
            {
                if (mapSr.IsGeographic || unit == null)
                {
                    prepared.AreaSquareMeters = GeometryEngine.Instance.GeodesicArea(geometry, AreaUnit.SquareMeters);
                    prepared.MeasureMethod = "大地线面积（平方米），按椭球计算";
                }
                else
                {
                    prepared.AreaSquareMeters = GeometryEngine.Instance.Area(geometry) * unit.MetersPerUnit * unit.MetersPerUnit;
                    prepared.MeasureMethod = "平面面积（平方米），按地图坐标系单位换算";
                }
            }
            else prepared.MeasureMethod = "点要素不计长度或面积";
            return prepared;
        }

        private static JsonNode DrawingPreviewResult(DrawingGeometry prepared, FeatureLayer target)
        {
            var geometry = prepared.Geometry;
            var extent = geometry.Extent;
            var sr = geometry.SpatialReference;
            return Node(new {
                status = "PREVIEW",
                geometry_type = geometry.GeometryType.ToString(),
                vertex_count = geometry.PointCount,
                extent = new {
                    xmin = extent.XMin, ymin = extent.YMin, xmax = extent.XMax, ymax = extent.YMax,
                    wkid = sr?.Wkid ?? 0, sr_name = sr?.Name ?? ""
                },
                crs = prepared.Crs,
                length_m = prepared.LengthMeters,
                area_sq_m = prepared.AreaSquareMeters,
                measure_method = prepared.MeasureMethod,
                target = target?.Name ?? DrawingTempTargetName,
                layer_uri = target?.URI ?? "",
                note = "confirm=true 才写入"
            });
        }

        private static FeatureLayer ResolveDrawingTarget(Map map, string layerInput, GeometryType geometryType, bool requireEditable)
        {
            var layer = FindLayer(map, layerInput) as FeatureLayer
                ?? throw new ArgumentException("目标不是要素图层，不能写入几何：" + layerInput);
            if (requireEditable && !layer.IsEditable)
                throw new InvalidOperationException("目标图层不可编辑（可能未启用编辑或数据源为只读）：" + layer.Name);
            var expected = ExpectedShapeType(geometryType);
            if (layer.ShapeType != expected)
                throw new ArgumentException("目标图层几何类型为" + DrawingShapeName(layer.ShapeType) + "，与本次绘制的" +
                    DrawingShapeName(expected) + "不一致，未写入。");
            return layer;
        }

        private static async Task<JsonNode> CreateDrawingFeatureAsync(string mapKey, FeatureLayer layer,
            DrawingGeometry prepared, string attributesText, CancellationToken token)
        {
            Dictionary<string, object> attributes = null;
            using (var featureClass = layer.GetFeatureClass())
            {
                if (featureClass == null) throw new InvalidOperationException("目标图层没有可写要素类：" + layer.Name);
                using (var definition = featureClass.GetDefinition())
                    attributes = ConvertDrawingAttributes(attributesText, definition, layer.Name);
            }
            token.ThrowIfCancellationRequested();
            // P2-6：先登记 PENDING 批次再提交写入；异常/取消时保留批次并标 UNKNOWN，避免“已提交但未登记”。
            var batch = DrawingRegister(mapKey, "feature", layer.URI, null, null,
                prepared.Geometry.GeometryType.ToString(), prepared.Geometry.PointCount);
            var executeStarted = false;
            try
            {
                var operation = new EditOperation { Name = "AI 绘制要素", ShowProgressor = false };
                var rowToken = attributes == null || attributes.Count == 0
                    ? operation.Create(layer, prepared.Geometry)
                    : operation.Create(layer, prepared.Geometry, attributes);
                executeStarted = true;
                var succeeded = await operation.ExecuteAsync();
                if (!succeeded)
                {
                    DrawingMarkUnknown(batch, "写入命令执行失败，要素可能已提交或未提交；请人工在目标图层核对后再处理。");
                    throw new InvalidOperationException("写入要素失败：" +
                        (string.IsNullOrWhiteSpace(operation.ErrorMessage) ? "未返回明确原因，请查看 Pro 编辑日志。" : operation.ErrorMessage));
                }
                // 提交成功后立即回读 OID；从这里开始不再抛取消，避免已提交要素停留在未登记状态。
                if (rowToken?.ObjectID is long createdOid)
                {
                    batch.Oids.Add(createdOid);
                    batch.State = "CREATED";
                }
                else
                {
                    batch.State = "CREATED_NO_OID";
                    batch.Warning = "要素已写入但 OID 未回读，无法按批次自动撤销；请人工核对后处理。";
                }
                var warning = token.IsCancellationRequested
                    ? "写入已提交并登记后才收到取消请求；要素不会回滚，可用 clear_drawings target=" + batch.Id + " 撤销该批次。"
                    : "";
                return Node(new {
                    status = "CREATED", batch_id = batch.Id, target = layer.Name, layer_uri = layer.URI,
                    oid = batch.Oids.Count > 0 ? (object)batch.Oids[0] : null, oids = batch.Oids.ToArray(),
                    vertex_count = prepared.Geometry.PointCount, geometry_type = batch.GeometryType,
                    length_m = prepared.LengthMeters, area_sq_m = prepared.AreaSquareMeters,
                    measure_method = prepared.MeasureMethod, state = batch.State, warning,
                    note = batch.State == "CREATED"
                        ? "已写入 1 个要素并登记批次；用 clear_drawings target=<batch_id> 可撤销该批次。"
                        : "已写入 1 个要素，但 OID 未回读（批次状态 CREATED_NO_OID）；该批次只能人工核对处理。"
                });
            }
            catch (Exception)
            {
                if (executeStarted)
                {
                    if (batch.State == "PENDING")
                        DrawingMarkUnknown(batch, "写入过程中被取消或异常，要素可能已提交；请人工在目标图层核对后再处理。");
                }
                else DrawingRemove(mapKey, batch); // 尚未提交，移除无残留的空批次
                throw;
            }
        }

        private static async Task<JsonNode> CreateDrawingTempAsync(Map map, string mapKey, string projectUri,
            DrawingGeometry prepared, string attributesText, CancellationToken token)
        {
            if (attributesText.Length > 0)
                throw new ArgumentException("临时图形层没有业务字段，请勿提供 attributes；如需写属性请用 layer 指定目标要素图层。");
            var parent = Path.Combine(DrawingTempRoot(), DrawingProjectHash(projectUri));
            Directory.CreateDirectory(parent);
            var stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture);
            var name = "AI_DRAW_" + stamp + "_" + Guid.NewGuid().ToString("N").Substring(0, 6);
            var gdbPath = Path.Combine(parent, name + ".gdb");
            var featureClassPath = Path.Combine(gdbPath, name);
            var gpGeometryType = prepared.Geometry.GeometryType switch {
                GeometryType.Point => "POINT",
                GeometryType.Multipoint => "MULTIPOINT",
                GeometryType.Polyline => "POLYLINE",
                GeometryType.Polygon => "POLYGON",
                _ => throw new ArgumentException("不支持的几何类型：" + prepared.Geometry.GeometryType)
            };
            // P2-6：先登记 PENDING 批次（含计划的临时 GDB/要素类路径），再建库/建类/写要素。
            var batch = DrawingRegister(mapKey, "temp", "", gdbPath, featureClassPath,
                prepared.Geometry.GeometryType.ToString(), prepared.Geometry.PointCount);
            try
            {
                token.ThrowIfCancellationRequested();
                // P2-13：GP 调用放在 UI 线程并显式 GPThread，随后再回 QueuedTask 建图层与编辑。
                var gdbResult = await UiTask(() => Geoprocessing.ExecuteToolAsync("management.CreateFileGDB",
                    Geoprocessing.MakeValueArray(parent, name), null, token, null, GPExecuteToolFlags.GPThread));
                if (gdbResult == null || gdbResult.IsFailed)
                    throw new InvalidOperationException("创建临时文件地理数据库失败：" + DrawingGpMessages(gdbResult));
                token.ThrowIfCancellationRequested();
                var featureClassResult = await UiTask(() => Geoprocessing.ExecuteToolAsync("management.CreateFeatureclass",
                    Geoprocessing.MakeValueArray(gdbPath, name, gpGeometryType, "", "DISABLED", "DISABLED", prepared.SpatialReferenceParameter),
                    null, token, null, GPExecuteToolFlags.GPThread));
                if (featureClassResult == null || featureClassResult.IsFailed)
                    throw new InvalidOperationException("创建临时要素类失败：" + DrawingGpMessages(featureClassResult));
                token.ThrowIfCancellationRequested();
                var layerUri = await QueuedTask.Run(async () => {
                    token.ThrowIfCancellationRequested();
                    var layer = LayerFactory.Instance.CreateLayer(new Uri(featureClassPath), map, 0, DrawingTempLayerName) as FeatureLayer;
                    if (layer == null) throw new InvalidOperationException("临时要素类不能加载为地图图层，未写入绘制。");
                    batch.LayerUri = layer.URI;
                    batch.TargetLayerUri = layer.URI;
                    if (!layer.IsEditable)
                        throw new InvalidOperationException("临时图层当前不可编辑（可能未启用编辑），未写入绘制。");
                    var operation = new EditOperation { Name = "AI 绘制临时图形", ShowProgressor = false };
                    var rowToken = operation.Create(layer, prepared.Geometry);
                    var succeeded = await operation.ExecuteAsync();
                    if (!succeeded)
                    {
                        batch.State = "UNKNOWN";
                        batch.Warning = "临时图形写入命令执行失败，要素可能已提交或未提交；请人工核对或清理。";
                        throw new InvalidOperationException("写入临时图形失败：" +
                            (string.IsNullOrWhiteSpace(operation.ErrorMessage) ? "未返回明确原因，请查看 Pro 编辑日志。" : operation.ErrorMessage));
                    }
                    if (rowToken?.ObjectID is long createdOid) batch.Oids.Add(createdOid);
                    else if (DrawingGuessLastObjectId(layer) is long guessed) batch.Oids.Add(guessed);
                    if (batch.Oids.Count == 0)
                    {
                        batch.State = "CREATED_NO_OID";
                        batch.Warning = "临时图形已写入但 OID 未回读；清理时按图层与 GDB 路径整体移除。";
                    }
                    else batch.State = "CREATED";
                    return layer.URI;
                });
                var warning = token.IsCancellationRequested
                    ? "写入已提交并登记后才收到取消请求；临时图形不会回滚，可用 clear_drawings target=临时图形 清理。"
                    : "";
                return Node(new {
                    status = "CREATED", batch_id = batch.Id, target = DrawingTempTargetName,
                    layer_uri = layerUri, fc_path = featureClassPath,
                    oid = batch.Oids.Count > 0 ? (object)batch.Oids[0] : null, oids = batch.Oids.ToArray(),
                    vertex_count = prepared.Geometry.PointCount, geometry_type = batch.GeometryType,
                    length_m = prepared.LengthMeters, area_sq_m = prepared.AreaSquareMeters,
                    measure_method = prepared.MeasureMethod, state = batch.State, warning,
                    note = "已写入本机 appdata 的临时 File GDB 图形层；用 clear_drawings target=临时图形 可删除该图层与临时数据。"
                });
            }
            catch (Exception)
            {
                if (batch.State == "PENDING")
                {
                    batch.State = "UNKNOWN";
                    batch.Warning = "临时图形创建/写入中断，可能残留临时图层或临时 GDB；请用 clear_drawings target=临时图形 清理，并人工核对。";
                }
                await DrawingCleanupTempAsync(map, batch, gdbPath);
                throw;
            }
        }

        // ---------- list_drawings ----------

        private static async Task<JsonNode> ListDrawingsAsync(CancellationToken token, string expectedProject, string expectedMap)
        {
            token.ThrowIfCancellationRequested();
            var map = await Ui(() => {
                CheckContext(expectedProject, expectedMap, "list_drawings");
                return MapView.Active?.Map ?? throw new InvalidOperationException("没有活动地图。请先打开地图视图。");
            });
            return await QueuedTask.Run(() => {
                token.ThrowIfCancellationRequested();
                var mapKey = DrawingMapKey(Project.Current?.URI ?? "", map.URI);
                var batches = DrawingSnapshot(mapKey);
                var layers = map.GetLayersAsFlattenedList();
                var layerUris = new HashSet<string>(layers.Select(l => l.URI), StringComparer.Ordinal);
                var tempLayers = batches.Where(b => b.TargetKind == "temp").Select(b => new {
                    batch_id = b.Id,
                    layer_uri = b.LayerUri,
                    present = layerUris.Contains(b.LayerUri),
                    state = b.State,
                    warning = b.Warning,
                    feature_class_path = b.FeatureClassPath,
                    geometry_type = b.GeometryType,
                    vertex_count = b.VertexCount,
                    created = b.Created
                }).ToArray();
                var rows = batches.OrderByDescending(b => b.Created).Take(20).Select(b => new {
                    batch_id = b.Id,
                    created = b.Created,
                    target = b.TargetKind == "temp" ? DrawingTempTargetName : DrawingLayerNameByUri(layers, b.TargetLayerUri),
                    target_kind = b.TargetKind,
                    target_layer_uri = b.TargetLayerUri,
                    layer_uri = b.LayerUri,
                    state = b.State,
                    warning = b.Warning,
                    feature_count = b.Oids.Count,
                    oids = b.Oids,
                    geometry_type = b.GeometryType,
                    vertex_count = b.VertexCount
                }).ToArray();
                return Node(new {
                    temp_layer_present = tempLayers.Any(t => t.present),
                    temp_layers = tempLayers,
                    batch_count = batches.Length,
                    batches = rows,
                    note = "只列出本进程本会话由 AI 助手创建并登记的绘制；不包含原有业务要素。" +
                        "状态 UNKNOWN/CREATED_NO_OID 的批次需要人工在图层中核对，clear_drawings 不会猜 OID 删除业务要素。"
                });
            });
        }

        // ---------- clear_drawings ----------

        private static async Task<JsonNode> ClearDrawingsAsync(JsonObject args, CancellationToken token,
            string expectedProject, string expectedMap)
        {
            token.ThrowIfCancellationRequested();
            if (args["confirm"] == null || !(bool)args["confirm"]) throw new ArgumentException("撤销绘制需要 confirm=true。");
            var rawTarget = ToolProtocol.String(args, "target").Trim();
            var map = await Ui(() => {
                CheckContext(expectedProject, expectedMap, "clear_drawings");
                return MapView.Active?.Map ?? throw new InvalidOperationException("没有活动地图。请先打开地图视图。");
            });
            return await QueuedTask.Run(async () => {
                token.ThrowIfCancellationRequested();
                var mapKey = DrawingMapKey(Project.Current?.URI ?? "", map.URI);
                var all = DrawingSnapshot(mapKey);
                var target = rawTarget;
                DrawingBatch[] selected;
                if (target.Length == 0 || target == "全部" || string.Equals(target, "all", StringComparison.OrdinalIgnoreCase))
                    selected = all;
                else if (target == DrawingTempTargetName || target == "临时")
                    selected = all.Where(b => b.TargetKind == "temp").ToArray();
                else
                    selected = all.Where(b => string.Equals(b.Id, target, StringComparison.OrdinalIgnoreCase)).ToArray();
                if (selected.Length == 0)
                    return Node(new {
                        status = "UNDONE", removed_features = 0, removed_layers = 0,
                        removed_batches = Array.Empty<string>(),
                        batches = all.Select(b => b.Id).ToArray(),
                        note = target.Length == 0 ? "当前地图没有本助手创建的绘制记录。" : "没有找到匹配的绘制记录：" + target
                    });
                var removedFeatures = 0;
                var removedLayers = 0;
                var removedIds = new List<string>();
                var warnings = new List<string>();
                var manualBatches = new List<DrawingBatch>();
                foreach (var batch in selected)
                {
                    token.ThrowIfCancellationRequested();
                    if (batch.TargetKind == "temp")
                    {
                        var layer = map.GetLayersAsFlattenedList().FirstOrDefault(l => l.URI == batch.LayerUri);
                        if (layer != null)
                        {
                            map.RemoveLayer(layer);
                            removedLayers++;
                        }
                        if (!string.IsNullOrWhiteSpace(batch.FcPath) && !DrawingDeferDeleteTempGdb(batch.FcPath, out var warning) &&
                            !string.IsNullOrWhiteSpace(warning)) warnings.Add(warning);
                        removedIds.Add(batch.Id);
                        DrawingRemove(mapKey, batch);
                        continue;
                    }
                    // P2-6：UNKNOWN/CREATED_NO_OID/PENDING 批次不能确认哪些要素是本助手写入的，
                    // 只提示人工核对并保留记录，绝不猜 OID 删除业务要素。
                    if (batch.State == "UNKNOWN" || batch.State == "CREATED_NO_OID" || batch.State == "PENDING")
                    {
                        warnings.Add("批次 " + batch.Id + " 状态为 " + (batch.State ?? "UNKNOWN") +
                            "：写入结果未确认或 OID 未记录，未按 OID 删除任何要素；请人工在目标图层核对后处理。");
                        manualBatches.Add(batch);
                        continue;
                    }
                    var feature = map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                        .FirstOrDefault(l => l.URI == batch.TargetLayerUri);
                    if (feature == null)
                    {
                        warnings.Add("批次 " + batch.Id + " 的目标图层已不在当前地图中，未删除要素。");
                        removedIds.Add(batch.Id);
                        DrawingRemove(mapKey, batch);
                        continue;
                    }
                    if (batch.Oids.Count == 0)
                    {
                        warnings.Add("批次 " + batch.Id + " 没有可用的 OID 记录，无法按 OID 撤销；请人工在 Pro 中核对后处理。");
                        manualBatches.Add(batch);
                        continue;
                    }
                    var existing = new List<long>();
                    using (var featureClass = feature.GetFeatureClass())
                    {
                        if (featureClass == null)
                        {
                            warnings.Add("批次 " + batch.Id + " 的目标图层没有可读要素类，未删除要素。");
                            removedIds.Add(batch.Id);
                            DrawingRemove(mapKey, batch);
                            continue;
                        }
                        using (var definition = featureClass.GetDefinition())
                        {
                            var oidField = definition.GetObjectIDField();
                            var filter = new QueryFilter {
                                WhereClause = oidField + " IN (" +
                                    string.Join(",", batch.Oids.Select(oid => oid.ToString(CultureInfo.InvariantCulture))) + ")",
                                SubFields = oidField
                            };
                            using var cursor = featureClass.Search(filter, false);
                            while (cursor.MoveNext())
                            {
                                token.ThrowIfCancellationRequested();
                                using var row = cursor.Current;
                                existing.Add(row.GetObjectID());
                            }
                        }
                    }
                    if (existing.Count == 0)
                    {
                        warnings.Add("批次 " + batch.Id + " 记录的 OID 在当前图层中已不存在，未删除要素。");
                        removedIds.Add(batch.Id);
                        DrawingRemove(mapKey, batch);
                        continue;
                    }
                    var operation = new EditOperation { Name = "AI 撤销绘制要素", ShowProgressor = false };
                    operation.Delete(feature, existing);
                    var succeeded = await operation.ExecuteAsync();
                    if (!succeeded)
                        throw new InvalidOperationException("撤销批次 " + batch.Id + " 失败：" +
                            (string.IsNullOrWhiteSpace(operation.ErrorMessage) ? "未返回明确原因，请查看 Pro 编辑日志。" : operation.ErrorMessage));
                    removedFeatures += existing.Count;
                    removedIds.Add(batch.Id);
                    DrawingRemove(mapKey, batch);
                }
                if (token.IsCancellationRequested)
                    warnings.Add("撤销过程中收到取消请求；已完成的删除不会回滚，请用 list_drawings 核对剩余批次。");
                var remaining = DrawingSnapshot(mapKey);
                return Node(new {
                    status = "UNDONE", removed_features = removedFeatures, removed_layers = removedLayers,
                    removed_batches = removedIds,
                    manual_batches = manualBatches.Select(b => new {
                        batch_id = b.Id, state = b.State, warning = b.Warning,
                        target_layer_uri = b.TargetLayerUri
                    }).ToArray(),
                    batches = remaining.OrderByDescending(b => b.Created).Select(b => b.Id).ToArray(),
                    warnings,
                    note = "只删除本助手登记并创建的要素/临时层；未按 where 条件批量删除业务数据。" +
                        "撤销按批次 OID 删除：若期间人工删除过 AI 要素且 OID 被复用，可能误删，建议先 list_drawings 核对。"
                });
            });
        }

        // ---------- GeoJSON 解析 ----------

        private static Geometry ParseGeoJsonGeometry(string text, SpatialReference spatialReference, CancellationToken token)
        {
            JsonNode root;
            try { root = JsonNode.Parse(text); }
            catch (JsonException ex) { throw new ArgumentException("geometry 不是合法 JSON 文本：" + ex.Message, ex); }
            if (root is not JsonObject geojson) throw new ArgumentException("geometry 必须是 GeoJSON 几何对象（JSON 对象）。");
            var type = ((string)geojson["type"] ?? "").Trim();
            if (type.Length == 0) throw new ArgumentException("GeoJSON 几何缺少 type。");
            if (geojson["coordinates"] is not JsonArray coordinates)
                throw new ArgumentException("GeoJSON 几何缺少 coordinates 数组。");
            token.ThrowIfCancellationRequested();
            var vertices = CountPositions(coordinates);
            if (vertices == 0) throw new ArgumentException("coordinates 中没有坐标点。");
            if (vertices > DrawingMaxVertices)
                throw new ArgumentException("顶点数 " + vertices + " 超过上限 " + DrawingMaxVertices + "，请拆分后分批绘制。");
            switch (type.ToLowerInvariant())
            {
                case "point":
                    return MapPointBuilderEx.CreateMapPoint(
                        ParsePosition(coordinates, spatialReference, "coordinates"), spatialReference);
                case "multipoint": {
                    var builder = new MultipointBuilderEx(spatialReference);
                    var items = RequireArray(coordinates, "coordinates");
                    for (var i = 0; i < items.Count; i++)
                        builder.AddPoint(ParsePosition(RequireArray(items[i], "coordinates[" + i + "]"),
                            spatialReference, "coordinates[" + i + "]"));
                    return builder.ToGeometry();
                }
                case "linestring":
                case "polyline": {
                    var builder = new PolylineBuilderEx(spatialReference);
                    builder.AddPart(ParsePolylinePart(coordinates, spatialReference, "coordinates"));
                    return builder.ToGeometry();
                }
                case "multilinestring": {
                    var builder = new PolylineBuilderEx(spatialReference);
                    var parts = RequireArray(coordinates, "coordinates");
                    for (var i = 0; i < parts.Count; i++)
                        builder.AddPart(ParsePolylinePart(RequireArray(parts[i], "coordinates[" + i + "]"),
                            spatialReference, "coordinates[" + i + "]"));
                    return builder.ToGeometry();
                }
                case "polygon":
                    return ParsePolygon(coordinates, spatialReference, "coordinates");
                case "multipolygon": {
                    var builder = new PolygonBuilderEx(spatialReference);
                    var polygons = RequireArray(coordinates, "coordinates");
                    for (var i = 0; i < polygons.Count; i++)
                        AddPolygonRings(builder, RequireArray(polygons[i], "coordinates[" + i + "]"),
                            spatialReference, "coordinates[" + i + "]");
                    return builder.ToGeometry();
                }
                default:
                    throw new ArgumentException("不支持的 GeoJSON 几何类型：" + type +
                        "（支持 Point/MultiPoint/Polyline/LineString/MultiLineString/Polygon/MultiPolygon）。");
            }
        }

        private static Polygon ParsePolygon(JsonArray rings, SpatialReference spatialReference, string path)
        {
            var builder = new PolygonBuilderEx(spatialReference);
            AddPolygonRings(builder, rings, spatialReference, path);
            return builder.ToGeometry();
        }

        private static void AddPolygonRings(PolygonBuilderEx builder, JsonArray rings, SpatialReference spatialReference, string path)
        {
            if (rings.Count == 0) throw new ArgumentException(path + " 至少需要 1 个环。");
            for (var i = 0; i < rings.Count; i++)
            {
                var ring = ParsePolygonRing(RequireArray(rings[i], path + "[" + i + "]"),
                    spatialReference, path + "[" + i + "]");
                // GeoJSON 约定外环逆时针、内环顺时针；ArcGIS 拓扑要求外环顺时针、内环逆时针。
                builder.AddPart(NormalizePolygonRing(ring, i == 0));
            }
        }

        private static List<MapPoint> NormalizePolygonRing(List<MapPoint> ring, bool exterior)
        {
            // 鞋带公式：有向面积 > 0 为逆时针，< 0 为顺时针（环已闭合，首尾点相同）。
            double signedArea2 = 0;
            for (var i = 0; i + 1 < ring.Count; i++)
                signedArea2 += ring[i].X * ring[i + 1].Y - ring[i + 1].X * ring[i].Y;
            var clockwise = signedArea2 < 0;
            if ((exterior && !clockwise) || (!exterior && clockwise)) ring.Reverse();
            return ring;
        }

        private static JsonArray RequireArray(JsonNode node, string path)
            => node as JsonArray ?? throw new ArgumentException(path + " 必须是坐标数组。");

        private static int CountPositions(JsonNode node)
        {
            if (node is not JsonArray array || array.Count == 0) return 0;
            if (array[0] is JsonValue) return 1;
            var total = 0;
            foreach (var item in array)
            {
                total += CountPositions(item);
                if (total > DrawingMaxVertices) return total;
            }
            return total;
        }

        private static MapPoint ParsePosition(JsonArray position, SpatialReference spatialReference, string path)
        {
            if (position.Count < 2 || position.Count > 4)
                throw new ArgumentException(path + " 坐标应为 [x, y]、[x, y, z] 或 [x, y, z, m]，实际有 " + position.Count + " 个值。");
            var x = ParseCoordinate(position[0], path + "[0]");
            var y = ParseCoordinate(position[1], path + "[1]");
            return MapPointBuilderEx.CreateMapPoint(x, y, spatialReference);
        }

        private static double ParseCoordinate(JsonNode node, string path)
        {
            if (node is null) throw new ArgumentException(path + " 不能为空。");
            var element = JsonSerializer.SerializeToElement(node);
            if (element.ValueKind != JsonValueKind.Number || !element.TryGetDouble(out var number) || !double.IsFinite(number))
                throw new ArgumentException(path + " 不是有限数值。");
            return number;
        }

        private static List<MapPoint> ParsePositions(JsonArray array, SpatialReference spatialReference, string path)
        {
            var points = new List<MapPoint>(array.Count);
            for (var i = 0; i < array.Count; i++)
                points.Add(ParsePosition(RequireArray(array[i], path + "[" + i + "]"), spatialReference, path + "[" + i + "]"));
            return points;
        }

        private static List<MapPoint> ParsePolylinePart(JsonArray array, SpatialReference spatialReference, string path)
        {
            var points = ParsePositions(array, spatialReference, path);
            if (points.Count < 2) throw new ArgumentException(path + " 作为线至少需要 2 个顶点（当前 " + points.Count + "）。");
            return points;
        }

        private static List<MapPoint> ParsePolygonRing(JsonArray array, SpatialReference spatialReference, string path)
        {
            var points = ParsePositions(array, spatialReference, path);
            if (points.Count < 3) throw new ArgumentException(path + " 作为面环至少需要 3 个顶点（当前 " + points.Count + "）。");
            var first = points[0];
            var last = points[points.Count - 1];
            if (first.X != last.X || first.Y != last.Y)
                points.Add(MapPointBuilderEx.CreateMapPoint(first.X, first.Y, spatialReference));
            if (points.Count < 4)
                throw new ArgumentException(path + " 作为面环闭合后至少需要 4 个顶点（当前 " + points.Count + "）。");
            return points;
        }

        // ---------- 属性 ----------

        private static Dictionary<string, object> ConvertDrawingAttributes(string text, FeatureClassDefinition definition, string layerName)
        {
            if (string.IsNullOrWhiteSpace(text)) return null;
            JsonNode root;
            try { root = JsonNode.Parse(text); }
            catch (JsonException ex) { throw new ArgumentException("attributes 不是合法 JSON 文本：" + ex.Message, ex); }
            if (root is not JsonObject attributes)
                throw new ArgumentException("attributes 必须是 JSON 对象，例如 {\"NAME\":\"示例\"}。");
            var fields = definition.GetFields();
            var values = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            foreach (var pair in attributes)
            {
                var field = fields.FirstOrDefault(f => string.Equals(f.Name, pair.Key, StringComparison.OrdinalIgnoreCase))
                    ?? throw new ArgumentException("目标图层 " + layerName + " 没有字段：" + pair.Key);
                if (field.FieldType == FieldType.OID || field.FieldType == FieldType.Geometry ||
                    field.FieldType == FieldType.GlobalID)
                    throw new ArgumentException("字段 " + field.Name + " 属于系统字段，不能作为绘制属性写入。");
                if (pair.Value == null) continue;
                values[field.Name] = ConvertDrawingAttributeValue(pair.Value, field);
            }
            return values;
        }

        private static object ConvertDrawingAttributeValue(JsonNode node, Field field)
        {
            var element = JsonSerializer.SerializeToElement(node);
            var kind = element.ValueKind;
            if (kind == JsonValueKind.Object || kind == JsonValueKind.Array)
                throw new ArgumentException("字段 " + field.Name + " 的值必须是文本、数值或布尔。");
            switch (field.FieldType)
            {
                case FieldType.String:
                    if (kind == JsonValueKind.String) return element.GetString();
                    if (kind == JsonValueKind.Number) return element.GetDouble().ToString("R", CultureInfo.InvariantCulture);
                    if (kind == JsonValueKind.True || kind == JsonValueKind.False)
                        return element.GetBoolean() ? "true" : "false";
                    return element.ToString();
                case FieldType.SmallInteger:
                case FieldType.Integer:
                case FieldType.BigInteger: {
                    if (kind == JsonValueKind.True || kind == JsonValueKind.False)
                        return element.GetBoolean() ? 1L : 0L;
                    if (kind == JsonValueKind.Number)
                    {
                        var number = element.GetDouble();
                        if (!double.IsFinite(number) || Math.Abs(number - Math.Round(number)) > 1e-9)
                            throw new ArgumentException("字段 " + field.Name + " 需要整数值。");
                        return Convert.ToInt64(Math.Round(number), CultureInfo.InvariantCulture);
                    }
                    if (kind == JsonValueKind.String &&
                        long.TryParse(element.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed))
                        return parsed;
                    throw new ArgumentException("字段 " + field.Name + " 需要整数值。");
                }
                case FieldType.Single:
                case FieldType.Double: {
                    if (kind == JsonValueKind.True || kind == JsonValueKind.False)
                        return element.GetBoolean() ? 1d : 0d;
                    if (kind == JsonValueKind.Number)
                    {
                        var number = element.GetDouble();
                        if (!double.IsFinite(number)) throw new ArgumentException("字段 " + field.Name + " 需要有限数值。");
                        return number;
                    }
                    if (kind == JsonValueKind.String &&
                        double.TryParse(element.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed) &&
                        double.IsFinite(parsed))
                        return parsed;
                    throw new ArgumentException("字段 " + field.Name + " 需要数值。");
                }
                default:
                    throw new ArgumentException("字段 " + field.Name + " 的类型为 " + field.FieldType +
                        "，当前仅支持文本、整型、浮点字段。");
            }
        }

        // ---------- 登记表 ----------

        private static string DrawingMapKey(string projectUri, string mapUri) => (projectUri ?? "") + "|" + (mapUri ?? "");

        private static void DrawingAdd(string mapKey, DrawingBatch batch)
        {
            var list = DrawingBatches.GetOrAdd(mapKey, _ => new List<DrawingBatch>());
            lock (list) list.Add(batch);
        }

        // P2-6：写入前先登记 PENDING 批次（含目标图层 URI、临时 GDB/要素类路径与计划几何类型），
        // 成功后再回填 OID/状态；异常或取消时批次保留为 UNKNOWN，供 list_drawings 显示与人工核对。
        private static DrawingBatch DrawingRegister(string mapKey, string targetKind, string targetLayerUri,
            string fcPath, string featureClassPath, string geometryType, int vertexCount)
        {
            var batch = new DrawingBatch {
                Id = NewDrawingBatchId(), Created = DateTimeOffset.Now, TargetKind = targetKind,
                TargetLayerUri = targetLayerUri ?? "", LayerUri = targetLayerUri ?? "",
                FcPath = fcPath, FeatureClassPath = featureClassPath,
                GeometryType = geometryType, VertexCount = vertexCount,
                Oids = new List<long>(), State = "PENDING"
            };
            DrawingAdd(mapKey, batch);
            return batch;
        }

        private static void DrawingMarkUnknown(DrawingBatch batch, string warning)
        {
            batch.State = "UNKNOWN";
            if (!string.IsNullOrWhiteSpace(warning)) batch.Warning = warning;
        }

        private static void DrawingRemove(string mapKey, DrawingBatch batch)
        {
            if (!DrawingBatches.TryGetValue(mapKey, out var list)) return;
            lock (list)
            {
                list.Remove(batch);
                if (list.Count == 0) DrawingBatches.TryRemove(mapKey, out _);
            }
        }

        private static DrawingBatch[] DrawingSnapshot(string mapKey)
        {
            if (!DrawingBatches.TryGetValue(mapKey, out var list)) return Array.Empty<DrawingBatch>();
            lock (list) return list.ToArray();
        }

        private static string NewDrawingBatchId()
            => "DRW-" + DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture) + "-" + Guid.NewGuid().ToString("N").Substring(0, 6);

        private static string DrawingLayerNameByUri(IReadOnlyList<Layer> layers, string uri)
        {
            if (string.IsNullOrWhiteSpace(uri)) return "";
            var layer = layers.FirstOrDefault(l => l.URI == uri);
            return layer?.Name ?? "(图层已移除)";
        }

        // ---------- 临时 GDB ----------

        private static string DrawingTempRoot()
            => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ProAiBridge", "drawings");

        private static string DrawingProjectHash(string projectUri)
        {
            if (string.IsNullOrWhiteSpace(projectUri)) return "noproject";
            return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(projectUri))).Substring(0, 8).ToLowerInvariant();
        }

        private static bool DrawingIsUnderTempRoot(string path)
        {
            try
            {
                var root = Path.GetFullPath(DrawingTempRoot()) + Path.DirectorySeparatorChar;
                var full = Path.GetFullPath(path);
                return full.Length > root.Length &&
                    full.StartsWith(root, StringComparison.OrdinalIgnoreCase) &&
                    full.EndsWith(".gdb", StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception) { return false; }
        }

        /// <summary>清理可处理的路径：drawings 根下的 .gdb，或清理过程中重命名出的 ...gdb.delete-&lt;guid&gt;。</summary>
        private static bool DrawingIsManageablePath(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return false;
            if (DrawingIsUnderTempRoot(path)) return true;
            try
            {
                var root = Path.GetFullPath(DrawingTempRoot()) + Path.DirectorySeparatorChar;
                var full = Path.GetFullPath(path);
                return full.Length > root.Length &&
                    full.StartsWith(root, StringComparison.OrdinalIgnoreCase) &&
                    full.Contains(".gdb.delete-", StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception) { return false; }
        }

        private static readonly object DrawingPendingGate = new object();

        private static string DrawingPendingPath => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "ProAiBridge", "drawings", "pending_delete.json");

        /// <summary>
        /// 把临时 GDB 登记为“下次启动 Pro 时删除”。不在 Pro 仍引用数据时递归删目录：
        /// File GDB 被部分删除会破坏数据源，实机曾因此在后续操作中导致 Pro 崩溃。
        /// 返回 true 表示没有需要登记的内容（路径为空或已不存在）。
        /// </summary>
        private static bool DrawingDeferDeleteTempGdb(string path, out string warning)
        {
            warning = "";
            if (string.IsNullOrWhiteSpace(path)) return true;
            if (!DrawingIsUnderTempRoot(path))
            {
                warning = "临时 GDB 路径不在助手绘图目录下，已拒绝删除：" + path;
                return false;
            }
            if (!Directory.Exists(path)) return true;
            try
            {
                lock (DrawingPendingGate)
                {
                    var pending = DrawingReadPending();
                    if (!pending.Contains(path, StringComparer.OrdinalIgnoreCase)) pending.Add(path);
                    DrawingWritePending(pending);
                }
                warning = "临时数据已登记，将在下次启动 ArcGIS Pro 时清理（本次 Pro 仍引用该 GDB，未递归删除以免损坏数据）：" + path;
            }
            catch (Exception ex)
            {
                warning = "临时数据未能登记清理：" + ex.Message + "；请关闭 Pro 后手动删除：" + path;
            }
            return false;
        }

        /// <summary>
        /// Pro 启动时清理上次会话遗留的临时 GDB。先原子重命名再递归删除：
        /// 若另一 Pro 实例/已自动打开的工程仍持有该 GDB 句柄，Move 会直接失败（不会部分删除），
        /// 保留 pending 下次再试；重命名成功后目录已无引用，递归删除安全。
        /// </summary>
        internal static void CleanupPendingDrawings()
        {
            try
            {
                var pending = DrawingReadPending();
                if (pending.Count == 0) return;
                var remaining = new List<string>();
                foreach (var path in pending)
                {
                    if (!DrawingIsManageablePath(path)) continue;
                    if (Directory.Exists(path))
                    {
                        if (path.EndsWith(".gdb", StringComparison.OrdinalIgnoreCase))
                        {
                            var trash = path + ".delete-" + Guid.NewGuid().ToString("N");
                            try { Directory.Move(path, trash); }
                            catch (Exception) { remaining.Add(path); continue; }
                            try { Directory.Delete(trash, true); }
                            catch (Exception) { remaining.Add(trash); }
                        }
                        else
                        {
                            // 上次清理已重命名但未删干净的残留（...gdb.delete-<guid>）。
                            try { Directory.Delete(path, true); }
                            catch (Exception) { remaining.Add(path); }
                        }
                    }
                }
                if (remaining.Count == 0)
                {
                    try { File.Delete(DrawingPendingPath); } catch (Exception) { }
                }
                else
                {
                    DrawingWritePending(remaining);
                }
                DrawingPruneEmptyFolders();
            }
            catch (Exception ex) { Trace.TraceError("清理遗留临时绘制数据失败：" + ex.Message); }
        }

        private static void DrawingPruneEmptyFolders()
        {
            try
            {
                var root = DrawingTempRoot();
                if (!Directory.Exists(root)) return;
                foreach (var directory in Directory.GetDirectories(root))
                {
                    try { Directory.Delete(directory, false); }
                    catch (Exception) { }
                }
            }
            catch (Exception) { }
        }

        private static List<string> DrawingReadPending()
        {
            try
            {
                if (!File.Exists(DrawingPendingPath)) return new List<string>();
                var node = JsonNode.Parse(File.ReadAllText(DrawingPendingPath));
                if (node is JsonArray array)
                    return array.OfType<JsonValue>()
                        .Select(value => (string)value)
                        .Where(value => !string.IsNullOrWhiteSpace(value))
                        .ToList();
            }
            catch (Exception) { }
            return new List<string>();
        }

        private static void DrawingWritePending(IEnumerable<string> paths)
        {
            var directory = Path.GetDirectoryName(DrawingPendingPath);
            if (!string.IsNullOrWhiteSpace(directory)) Directory.CreateDirectory(directory);
            var array = new JsonArray(paths.Select(path => (JsonNode)JsonValue.Create(path)).ToArray());
            // 原子替换，避免多实例/崩溃留下半截 JSON。
            var temp = DrawingPendingPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temp, array.ToJsonString());
                File.Move(temp, DrawingPendingPath, true);
            }
            finally
            {
                if (File.Exists(temp))
                {
                    try { File.Delete(temp); } catch (Exception) { }
                }
            }
        }

        // 失败/取消后的尽力清理：移除已登记的临时图层并尝试删除临时 GDB；清理失败只记 Trace，不覆盖原始异常。
        private static async Task DrawingCleanupTempAsync(Map map, DrawingBatch batch, string gdbPath)
        {
            if (!string.IsNullOrWhiteSpace(batch.LayerUri))
            {
                try
                {
                    await QueuedTask.Run(() => {
                        var layer = map.GetLayersAsFlattenedList().FirstOrDefault(l => l.URI == batch.LayerUri);
                        if (layer != null) map.RemoveLayer(layer);
                    });
                }
                catch (Exception ex) { Trace.TraceError("清理临时绘制图层失败：" + ex.Message); }
            }
            try { DrawingDeferDeleteTempGdb(gdbPath, out _); }
            catch (Exception ex) { Trace.TraceError("登记临时绘制 GDB 清理失败：" + ex.Message); }
        }

        private static long? DrawingGuessLastObjectId(FeatureLayer layer)
        {
            try
            {
                using var featureClass = layer.GetFeatureClass();
                using var definition = featureClass.GetDefinition();
                var oidField = definition.GetObjectIDField();
                var filter = new QueryFilter { SubFields = oidField, PostfixClause = "ORDER BY " + oidField + " DESC" };
                using var cursor = featureClass.Search(filter, false);
                if (cursor.MoveNext())
                {
                    using var row = cursor.Current;
                    return row.GetObjectID();
                }
            }
            catch (Exception ex) { Trace.TraceError("回读临时绘制要素 OID 失败：" + ex.Message); }
            return null;
        }

        private static string DrawingGpMessages(IGPResult result)
            => result == null ? "地理处理未返回结果。" : string.Join("; ", result.Messages.Select(m => m.Text));

        private static esriGeometryType ExpectedShapeType(GeometryType geometryType) => geometryType switch {
            GeometryType.Point => esriGeometryType.esriGeometryPoint,
            GeometryType.Multipoint => esriGeometryType.esriGeometryMultipoint,
            GeometryType.Polyline => esriGeometryType.esriGeometryPolyline,
            GeometryType.Polygon => esriGeometryType.esriGeometryPolygon,
            _ => throw new ArgumentException("不支持的几何类型：" + geometryType)
        };

        private static string DrawingShapeName(esriGeometryType shapeType) => shapeType switch {
            esriGeometryType.esriGeometryPoint => "点",
            esriGeometryType.esriGeometryMultipoint => "多点",
            esriGeometryType.esriGeometryPolyline => "线",
            esriGeometryType.esriGeometryPolygon => "面",
            _ => shapeType.ToString()
        };
    }
}
