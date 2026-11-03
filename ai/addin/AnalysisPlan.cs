using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;

namespace ProAiBridge
{
    internal sealed class FeatureSource
    {
        public string Path { get; set; }
        public string Geometry { get; set; }
        public string SpatialReference { get; set; }
        public bool Geographic { get; set; }
        public string Datum { get; set; }
        public Dictionary<string, string> Fields { get; set; }
    }

    // Shared by the SDK adapter and executable contract tests; no GP execution here.
    internal sealed class AnalysisPlan
    {
        public string Tool { get; set; }
        public string[] Parameters { get; set; }

        internal static AnalysisPlan Prepare(string name, JsonObject args, FeatureSource input, FeatureSource secondary = null, string[] transformations = null)
        {
            if (string.IsNullOrEmpty(input?.SpatialReference)) throw new ArgumentException("输入数据空间参考未知，请先定义正确坐标系。");
            var output = ToolProtocol.String(args, "output");
            string tool; string[] parameters;
            if (name == "buffer_features")
            {
                var distance = (double)args["distance"];
                if (!double.IsFinite(distance) || distance == 0 || (distance < 0 && input.Geometry != "Polygon"))
                    throw new ArgumentException("缓冲距离须非零；仅面要素支持负距离。");
                var units = new Dictionary<string, string> { ["meters"] = "Meters", ["kilometers"] = "Kilometers", ["feet"] = "Feet" };
                if (!units.TryGetValue(ToolProtocol.String(args, "unit"), out var unit)) throw new ArgumentException("请选择 meters、kilometers 或 feet 距离单位。");
                var method = ToolProtocol.String(args, "method", "GEODESIC");
                if (method != "GEODESIC" && method != "PLANAR") throw new ArgumentException("缓冲方法无效。");
                if (method == "PLANAR" && input.Geographic) throw new ArgumentException("PLANAR 需要投影坐标系；经纬度数据请用 GEODESIC 或先投影。");
                tool = "analysis.Buffer";
                parameters = new[] { input.Path, output, distance.ToString("R", CultureInfo.InvariantCulture) + " " + unit,
                    "FULL", "ROUND", (bool?)args["dissolve"] == true ? "ALL" : "NONE", "#", method };
            }
            else if (name == "clip_features")
            {
                if (secondary?.Geometry != "Polygon") throw new ArgumentException("裁剪范围必须为面要素。");
                if (string.IsNullOrEmpty(secondary.SpatialReference) || input.SpatialReference != secondary.SpatialReference)
                    throw new ArgumentException("输入和裁剪范围坐标系须一致，请先投影后再裁剪。");
                tool = "analysis.Clip"; parameters = new[] { input.Path, secondary.Path, output };
            }
            else if (name == "project_features")
            {
                var wkid = ToolProtocol.Int(args, "target_wkid", 0);
                if (wkid <= 0 || string.IsNullOrEmpty(secondary?.SpatialReference)) throw new ArgumentException("目标坐标系无效，请使用有效 WKID。");
                var transformation = ToolProtocol.String(args, "transformation");
                if (transformation.Length > 0 && !(transformations ?? Array.Empty<string>()).Contains(transformation))
                    throw new ArgumentException("坐标变换不在本机候选清单中，请先 list_transformations。");
                if ((string.IsNullOrEmpty(input.Datum) || input.Datum != secondary.Datum) && transformation.Length == 0)
                    throw new ArgumentException("跨基准面投影需要明确选择坐标变换，请先 list_transformations；没有候选时不能自动猜测。");
                tool = "management.Project";
                parameters = new[] { input.Path, output, wkid.ToString(CultureInfo.InvariantCulture), transformation.Length == 0 ? "#" : transformation };
            }
            else if (name == "spatial_join_features")
            {
                if (string.IsNullOrEmpty(secondary?.SpatialReference) || input.SpatialReference != secondary.SpatialReference)
                    throw new ArgumentException("空间连接双方须具有相同已知坐标系，请先核对输入；确需投影时，应明确选择目标坐标系后操作。");
                var shapes = new[] { "Point", "Multipoint", "Polyline", "Polygon" };
                if (!shapes.Contains(input.Geometry) || !shapes.Contains(secondary.Geometry)) throw new ArgumentException("空间连接需要点、线或面要素。");
                var operation = ToolProtocol.String(args, "join_operation", "ONE_TO_MANY");
                var match = ToolProtocol.String(args, "match", "INTERSECT");
                if (!new[] { "ONE_TO_ONE", "ONE_TO_MANY" }.Contains(operation) || !new[] { "INTERSECT", "WITHIN", "CONTAINS" }.Contains(match))
                    throw new ArgumentException("空间连接关系或匹配方式无效。");
                tool = "analysis.SpatialJoin";
                parameters = new[] { input.Path, secondary.Path, output, "JOIN_" + operation,
                    (bool?)args["keep_all"] == false ? "KEEP_COMMON" : "KEEP_ALL", "#", match };
            }
            else if (name == "dissolve_features")
            {
                var fields = new List<string>();
                foreach (var value in args["fields"] as JsonArray ?? new JsonArray())
                {
                    var field = input.Fields.SingleOrDefault(p => string.Equals(p.Key, (string)value, StringComparison.OrdinalIgnoreCase));
                    if (field.Key == null || new[] { "Geometry", "Blob", "Raster" }.Contains(field.Value)) throw new ArgumentException("融合字段不存在或类型不支持：" + value);
                    if (fields.Contains(field.Key)) throw new ArgumentException("融合字段重复：" + value);
                    fields.Add(field.Key);
                }
                tool = "management.Dissolve";
                parameters = new[] { input.Path, output, fields.Count == 0 ? "#" : string.Join(";", fields), "#",
                    (bool?)args["multipart"] == false ? "SINGLE_PART" : "MULTI_PART", "DISSOLVE_LINES" };
            }
            else throw new ArgumentException("未知具名分析工具。");
            return new AnalysisPlan { Tool = tool, Parameters = PaneState.PrepareGpParameters(tool, parameters,
                _ => throw new ArgumentException("分析输入须先固定到数据源。")) };
        }

        internal static int MoveIndex(string[] siblings, string moving, string reference, bool after)
        {
            if (moving == reference || !siblings.Contains(moving) || !siblings.Contains(reference))
                throw new ArgumentException("排序需要两个不同的同级图层。");
            return Array.IndexOf(siblings.Where(id => id != moving).ToArray(), reference) + (after ? 1 : 0);
        }
    }
}
