using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using ArcGIS.Core.Data;
using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace AcceptanceAddin
{
    internal static class LiveMcpBridge
    {
        internal const string PipeName = "arcgis-pro-live";
        private const int MaxRequestChars = 4096;
        private const int MaxResponseBytes = 1_000_000;
        private static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(20);
        private static int _started;
        private static readonly CancellationTokenSource StopToken = new CancellationTokenSource();
        internal static void Stop() => StopToken.Cancel();

        internal static void Start()
        {
            if (Interlocked.Exchange(ref _started, 1) != 0) return;
            _ = Task.Run(ListenAsync);
        }

        private static async Task ListenAsync()
        {
            while (!StopToken.IsCancellationRequested)
            {
                try
                {
                    using var pipe = new NamedPipeServerStream(PipeName, PipeDirection.InOut, 1,
                        PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                    using var deadline = CancellationTokenSource.CreateLinkedTokenSource(StopToken.Token);
                    deadline.CancelAfter(RequestTimeout);
                    await pipe.WaitForConnectionAsync(deadline.Token);
                    using var reader = new StreamReader(pipe, new UTF8Encoding(false), false, 1024, true);
                    using var writer = new StreamWriter(pipe, new UTF8Encoding(false), 1024, true) { AutoFlush = true };
                    var line = await ReadLineBoundedAsync(reader, MaxRequestChars, deadline.Token);
                    object response;
                    try { response = await DispatchAsync(line, deadline.Token); }
                    catch (OperationCanceledException) when (deadline.IsCancellationRequested) { throw; }
                    catch (Exception ex) { response = new { status = "ERROR", message = WorkbenchDiagnostics.Redact(ex.Message), diagnostic = WorkbenchDiagnostics.Error(ex, "bridge") }; }
                    var serialized = JsonSerializer.Serialize(response);
                    if (Encoding.UTF8.GetByteCount(serialized) > MaxResponseBytes)
                        serialized = JsonSerializer.Serialize(new { status = "ERROR", message = "ArcGIS Pro 响应超过 1 MB 限制。" });
                    await writer.WriteLineAsync(serialized.AsMemory(), deadline.Token);
                }
                catch (OperationCanceledException) when (StopToken.IsCancellationRequested) { break; }
                catch (Exception ex)
                {
                    System.Diagnostics.Trace.WriteLine("ArcGIS live MCP bridge: " + ex.Message);
                    await Task.Delay(1000);
                }
            }
        }

        private static async Task<string> ReadLineBoundedAsync(StreamReader reader, int limit, CancellationToken cancellationToken)
        {
            var result = new StringBuilder();
            var one = new char[1];
            while (result.Length <= limit)
            {
                var count = await reader.ReadAsync(one.AsMemory(0, 1), cancellationToken);
                if (count == 0 || one[0] == '\n') break;
                if (one[0] != '\r') result.Append(one[0]);
            }
            if (result.Length > limit) throw new InvalidDataException("MCP 请求超过长度限制。");
            if (result.Length == 0) throw new InvalidDataException("MCP 请求为空。");
            return result.ToString();
        }

        private static async Task<object> DispatchAsync(string line, CancellationToken cancellationToken)
        {
            using var document = JsonDocument.Parse(line);
            var root = document.RootElement;
            if (!root.TryGetProperty("action", out var actionValue) || actionValue.ValueKind != JsonValueKind.String)
                throw new InvalidDataException("缺少 action。");
            var action = actionValue.GetString();
            if (action == "status" || action == "list_layers") return await MapStatusAsync(action == "list_layers", cancellationToken);
            if (action != "search_parcel" && action != "zoom_to_parcel") throw new InvalidDataException("不支持的操作。");
            if (action == "zoom_to_parcel" && !LiveMcpProtocol.IsZoomConfirmed(root))
                throw new InvalidDataException("缩放请求必须明确携带 confirm=true。");

            var query = StringArg(root, "query", true);
            var layerUri = StringArg(root, "layer_uri", false);
            var fieldName = StringArg(root, "field", false);
            using var job = action == "zoom_to_parcel" ? WorkbenchJobService.Begin("地块定位") : null;
            try
            {
                var result = await SearchParcelAsync(query, layerUri, fieldName, action == "zoom_to_parcel", cancellationToken);
                job?.Finish("Succeeded"); return result;
            }
            catch { job?.Finish("Failed"); throw; }
        }

        private static string StringArg(JsonElement root, string name, bool required)
        {
            if (!root.TryGetProperty(name, out var value) || value.ValueKind == JsonValueKind.Null)
            {
                if (required) throw new InvalidDataException("缺少参数：" + name);
                return "";
            }
            if (value.ValueKind != JsonValueKind.String) throw new InvalidDataException("参数类型错误：" + name);
            var text = value.GetString() ?? "";
            if (text.Length > (name == "query" ? 128 : 1024)) throw new InvalidDataException("参数长度超过限制：" + name);
            if (required && string.IsNullOrWhiteSpace(text)) throw new InvalidDataException("请输入地块编号。");
            return text.Trim();
        }

        private static async Task<object> MapStatusAsync(bool includeLayers, CancellationToken cancellationToken)
        {
            var view = await OnUiAsync(() => MapView.Active, cancellationToken);
            if (view?.Map == null) return new { status = "NEEDS_MAP", message = "请在 ArcGIS Pro 中打开地图视图。" };
            var map = view.Map;
            return await QueuedTask.Run(() =>
            {
                var layers = map.GetLayersAsFlattenedList().Select(layer => new
                {
                    name = layer.Name,
                    uri = layer.URI,
                    type = layer.GetType().Name,
                    visible = layer.IsVisible
                }).ToList();
                return (object)new
                {
                    status = "PASS",
                    process_id = System.Diagnostics.Process.GetCurrentProcess().Id,
                    map_name = map.Name,
                    map_uri = map.URI,
                    layer_count = layers.Count,
                    layers = includeLayers ? layers : null
                };
            });
        }

        private sealed class ParcelHit
        {
            public FeatureLayer Layer { get; set; }
            public long Oid { get; set; }
            public string LayerName { get; set; }
            public string LayerUri { get; set; }
            public string Field { get; set; }
            public string ParcelId { get; set; }
        }

        private static async Task<object> SearchParcelAsync(string query, string layerUri, string requestedField, bool zoom,
            CancellationToken cancellationToken)
        {
            var view = await OnUiAsync(() => MapView.Active, cancellationToken);
            if (view?.Map == null) return new { status = "NEEDS_MAP", message = "请在 ArcGIS Pro 中打开地图视图。" };
            var map = view.Map;
            var result = await QueuedTask.Run(() =>
            {
                var polygonLayers = map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                    .Where(layer => layer.ShapeType == esriGeometryType.esriGeometryPolygon).ToList();
                FeatureLayer layer = null;
                if (!string.IsNullOrEmpty(layerUri)) layer = polygonLayers.FirstOrDefault(item => item.URI == layerUri);
                else if (polygonLayers.Count == 1) layer = polygonLayers[0];
                if (layer == null)
                    return (new List<ParcelHit>(), polygonLayers.Select(item => (object)new { name = item.Name, uri = item.URI }).ToList(), "NEEDS_LAYER");

                using var table = layer.GetTable();
                using var definition = table.GetDefinition();
                var fields = definition.GetFields();
                var autoField = ParcelNavigation.SearchField(layer.Name, fields.Select(item => item.Name));
                var selectedField = string.IsNullOrEmpty(requestedField) ? autoField : requestedField;
                var field = fields.FirstOrDefault(item => item.Name.Equals(selectedField, StringComparison.OrdinalIgnoreCase));
                if (field == null || !(field.FieldType == FieldType.String || field.FieldType == FieldType.Integer ||
                    field.FieldType == FieldType.SmallInteger || field.FieldType == FieldType.OID))
                    throw new InvalidDataException("未找到可精确检索的编号字段；请先列出图层并指定有效字段。");
                if (!Regex.IsMatch(field.Name, @"^[\p{L}_][\p{L}\p{N}_]*$"))
                    throw new InvalidDataException("编号字段名称包含不支持的字符，已停止查询。");

                string literal;
                if (field.FieldType == FieldType.String) literal = "'" + query.Replace("'", "''") + "'";
                else if (long.TryParse(query, System.Globalization.NumberStyles.Integer,
                    System.Globalization.CultureInfo.InvariantCulture, out var numeric))
                    literal = numeric.ToString(System.Globalization.CultureInfo.InvariantCulture);
                else throw new InvalidDataException("该编号字段为数字类型，请输入纯数字编号。");

                using var cursor = layer.Search(new QueryFilter { WhereClause = field.Name + " = " + literal, SubFields = "*" });
                var hits = new List<ParcelHit>();
                while (cursor.MoveNext() && hits.Count < 50)
                {
                    using var row = cursor.Current;
                    hits.Add(new ParcelHit { Layer = layer, Oid = row.GetObjectID(), LayerName = layer.Name,
                        LayerUri = layer.URI, Field = field.Name, ParcelId = Convert.ToString(row[field.Name],
                            System.Globalization.CultureInfo.InvariantCulture) ?? "" });
                }
                return (hits, new List<object>(), "PASS");
            });

            if (result.Item3 != "PASS") return new { status = result.Item3, message = "请从候选面图层中选择目标图层。", layers = result.Item2 };
            if (result.Item1.Count == 0) return new { status = "NOT_FOUND", message = "未找到该编号。", query };
            var candidates = result.Item1.Select(hit => new { layer = hit.LayerName, layer_uri = hit.LayerUri,
                field = hit.Field, parcel_id = hit.ParcelId, oid = hit.Oid }).ToList();
            if (!zoom) return new { status = "PASS", count = candidates.Count, hits = candidates };
            if (result.Item1.Count != 1) return new { status = "AMBIGUOUS", message = "编号命中多条记录；未改变地图选择，请先指定图层。", count = candidates.Count, hits = candidates };

            var selectedHit = result.Item1[0];
            var stillActive = await OnUiAsync(() => MapView.Active?.Map?.URI == map.URI, cancellationToken);
            if (!stillActive) return new { status = "MAP_CHANGED", message = "活动地图已变化；未定位，请重新查询。" };
            await QueuedTask.Run(() =>
            {
                selectedHit.Layer.SetVisibility(true);
                map.ClearSelection();
                using var selection = selectedHit.Layer.Select(new QueryFilter
                    { ObjectIDs = new List<long> { selectedHit.Oid }, SubFields = "*" }, SelectionCombinationMethod.New);
                if (selection.GetCount() != 1) throw new InvalidOperationException("地块已变化，请重新查询。");
            });
            var zoomed = await OnUiTaskAsync(() => view.ZoomToSelectedAsync(TimeSpan.FromSeconds(0.6)), cancellationToken);
            if (zoomed) return new { status = "PASS", message = "已选中并缩放到地块。", selected = candidates[0] };
            return new { status = "ERROR", message = "ArcGIS Pro 未能缩放到选中的地块。" };
        }

        private static Task<T> OnUiAsync<T>(Func<T> callback, CancellationToken cancellationToken)
            => Application.Current.Dispatcher.InvokeAsync(callback, DispatcherPriority.Normal, cancellationToken).Task;

        private static async Task<T> OnUiTaskAsync<T>(Func<Task<T>> callback, CancellationToken cancellationToken)
            => await Application.Current.Dispatcher.InvokeAsync(callback, DispatcherPriority.Normal, cancellationToken).Task.Unwrap();
    }
}
