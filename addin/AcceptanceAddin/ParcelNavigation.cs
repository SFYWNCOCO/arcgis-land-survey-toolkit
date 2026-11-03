using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading;

namespace AcceptanceAddin
{
    internal sealed class NavigationGate
    {
        private int _entered;
        internal bool TryEnter() => Interlocked.CompareExchange(ref _entered, 1, 0) == 0;
        internal void Exit() => Volatile.Write(ref _entered, 0);
    }
    internal sealed class ParcelReference
    {
        public string ProjectId { get; set; }
        public string LayerUri { get; set; }
        public long Oid { get; set; }
        public string ParcelId { get; set; }
        public string UnitId { get; set; }
        public string PointId { get; set; }
        public string SampleId { get; set; }
        public string ReplicateNo { get; set; }
        public string Category { get; set; }
        public string Detail { get; set; }
        public override string ToString()
        {
            var labels = new Dictionary<string, string> { {"unit_checks","单元检查"}, {"parcel_checks","图斑覆盖"},
                {"spacing_violations","点位间距"}, {"spacing_deviations","容重间距"}, {"issues","资料检查"},
                {"duplicate_point_ids","点号重复"}, {"duplicate_profiles","调查点重复"} };
            var label = labels.TryGetValue(Category ?? "", out var text) ? text : Category?.Contains("_") == true ? "采样检查" : Category;
            return label + " / " + string.Join(" / ", new[] { UnitId, ParcelId, PointId, SampleId, ReplicateNo }.Where(v => !string.IsNullOrEmpty(v)))
                + (string.IsNullOrEmpty(Detail) ? "" : "：" + Detail);
        }
    }

    internal sealed class SearchFieldCandidate
    {
        internal string Name { get; set; }
        internal int LayerCount { get; set; }
    }

    internal static class ParcelNavigation
    {
        internal static List<ParcelReference> Partition(IEnumerable<ParcelReference> rows, string project, string uri)
            => rows.Where(row => row.ProjectId == project && row.LayerUri == uri).OrderBy(row => row.Oid).ToList();
        internal static List<ParcelReference> ForNavigation(IEnumerable<ParcelReference> rows, ParcelReference selected)
        {
            var partition = Partition(rows, selected.ProjectId, selected.LayerUri);
            if (string.IsNullOrEmpty(selected.ProjectId) || selected.ProjectId == "未填")
                partition = partition.Where(row => !string.IsNullOrEmpty(selected.UnitId) ? row.UnitId == selected.UnitId : row.Oid == selected.Oid).ToList();
            return partition;
        }
        internal static string SearchField(string role, IEnumerable<string> names)
        {
            var candidates = role == "评价单元" ? new[] { "PJDYBH", "DYBH", "EVAL_UNIT", "DKBH" }
                : new[] { "DIKUAI_NO", "DKBH", "DKBM", "BSM", "PL_NAME", "PJDYBH" };
            return candidates.Select(candidate => names.FirstOrDefault(name => name.Equals(candidate, StringComparison.OrdinalIgnoreCase))).FirstOrDefault(name => name != null);
        }
        internal static List<SearchFieldCandidate> SearchFieldCoverage(IEnumerable<IEnumerable<string>> fieldsByLayer)
        {
            var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            foreach (var fields in fieldsByLayer ?? Enumerable.Empty<IEnumerable<string>>())
            {
                var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var rawName in fields ?? Enumerable.Empty<string>())
                {
                    var name = rawName?.Trim();
                    if (string.IsNullOrEmpty(name) || !seen.Add(name)) continue;
                    counts[name] = counts.TryGetValue(name, out var count) ? count + 1 : 1;
                }
            }
            return counts.Select(item => new SearchFieldCandidate { Name = item.Key, LayerCount = item.Value })
                .OrderByDescending(item => item.LayerCount)
                .ThenBy(item => item.Name, StringComparer.OrdinalIgnoreCase)
                .ToList();
        }
        internal static string ResolveSearchField(string requested, IEnumerable<string> names)
        {
            if (string.IsNullOrWhiteSpace(requested)) return null;
            return (names ?? Enumerable.Empty<string>()).FirstOrDefault(name =>
                !string.IsNullOrEmpty(name) && name.Equals(requested.Trim(), StringComparison.OrdinalIgnoreCase));
        }
        internal static string SearchFieldForLayer(string requested, string automatic, IEnumerable<string> names)
        {
            var candidate = string.IsNullOrWhiteSpace(requested) ? automatic : requested;
            return ResolveSearchField(candidate, names);
        }
        internal static string SearchFieldMissingReason(string requested, bool objectIdSelected)
        {
            if (objectIdSelected) return "缺少对象ID字段";
            if (!string.IsNullOrWhiteSpace(requested)) return "不含所选字段";
            return "缺少可识别的业务编号字段";
        }
        internal static bool HasIssueFields(ParcelReference issue, IEnumerable<string> names)
        {
            if (issue == null) return false;
            var fields = new HashSet<string>(names ?? Enumerable.Empty<string>(), StringComparer.OrdinalIgnoreCase);
            return (string.IsNullOrEmpty(issue.PointId) || fields.Contains("POINT_ID") || fields.Contains("CYDBH"))
                && (string.IsNullOrEmpty(issue.SampleId) || fields.Contains("SAMPLE_ID"))
                && (string.IsNullOrEmpty(issue.ReplicateNo) || fields.Contains("REP_NO"));
        }
        internal static int NextIndex(int current, int direction, int count)
            => count == 0 ? -1 : Math.Max(0, Math.Min(count - 1, current + direction));
        internal static IReadOnlyList<long> SelectionIds(bool onlySelected, IEnumerable<long> ids)
        {
            if (!onlySelected) return null;
            var selected = ids.Distinct().OrderBy(value => value).ToList();
            if (selected.Count == 0) throw new InvalidOperationException("当前没有选中要素；请先选择，或取消仅遍历选择范围。");
            return selected;
        }
        private static string Value(JsonElement row, string key)
            => row.ValueKind == JsonValueKind.Object && row.TryGetProperty(key, out var value) && value.ValueKind != JsonValueKind.Null ? value.ToString() : null;
        internal static bool Matches(ParcelReference issue, ParcelReference row)
        {
            bool Match(string a, string b) => string.IsNullOrEmpty(a) || a == b;
            return Match(issue.ProjectId, row.ProjectId) && Match(issue.UnitId, row.UnitId)
                && Match(issue.ParcelId, row.ParcelId) && Match(issue.PointId, row.PointId)
                && Match(issue.SampleId, row.SampleId) && Match(issue.ReplicateNo, row.ReplicateNo);
        }
        internal static List<ParcelReference> ReadQa(JsonElement qa, string label, bool bulk = false)
        {
            var result = new List<ParcelReference>();
            foreach (var property in qa.EnumerateObject())
            {
                if (property.Value.ValueKind != JsonValueKind.Array || property.Name == "manual_review_required") continue;
                foreach (var row in property.Value.EnumerateArray())
                {
                    if (Value(row, "status") == "PASS") continue;
                    if (row.ValueKind == JsonValueKind.String)
                    {
                        if (property.Name == "duplicate_point_ids") result.Add(new ParcelReference { Category = property.Name, PointId = row.GetString() });
                        else if (property.Name == "duplicate_profiles" || property.Name == "duplicate_sample_units") result.Add(new ParcelReference { Category = property.Name, UnitId = row.GetString() });
                        continue;
                    }
                    if (row.ValueKind != JsonValueKind.Object) continue;
                    var unit = Value(row, "unit_id");
                    if (property.Name == "spacing_violations" || property.Name == "spacing_deviations")
                    {
                        foreach (var suffix in new[] { "a", "b" })
                            result.Add(new ParcelReference { Category = property.Name, UnitId = unit,
                                ParcelId = Value(row, "parcel_id_" + suffix), PointId = Value(row, "point_id_" + suffix),
                                SampleId = Value(row, suffix == "a" ? "sample_id" : "sample_id_other"),
                                ReplicateNo = Value(row, "replicate_no_" + suffix) });
                        continue;
                    }
                    var item = new ParcelReference { Category = property.Name, UnitId = unit, ParcelId = Value(row, "parcel_id"),
                        PointId = Value(row, "point_id"), SampleId = Value(row, "sample_id"), ReplicateNo = Value(row, "replicate_no"),
                        Detail = Value(row, "detail") ?? Value(row, "reason") };
                    if (new[] { item.UnitId, item.ParcelId, item.PointId, item.SampleId }.Any(v => !string.IsNullOrEmpty(v))) result.Add(item);
                    if (Value(row, "other_parcel_id") != null)
                        result.Add(new ParcelReference { Category = property.Name, UnitId = Value(row, "other_unit_id"), ParcelId = Value(row, "other_parcel_id") });
                }
            }
            return result;
        }
        internal static List<ParcelReference> ReadReport(JsonElement report)
        {
            var result = new List<ParcelReference>();
            if (report.TryGetProperty("已有单元质检", out var existing) && existing.ValueKind == JsonValueKind.Object)
                result.AddRange(ReadQa(existing, "已有单元质检"));
            if (report.TryGetProperty("容重候选", out var bulk) && bulk.TryGetProperty("qa", out var bulkQa))
                result.AddRange(ReadQa(bulkQa, "容重质检", true));
            if (report.TryGetProperty("网格单元明细", out var units))
                foreach (var row in units.EnumerateArray())
                {
                    var unit = Value(row, "评价单元");
                    if (row.TryGetProperty("间距达标", out var spacing) && spacing.ValueKind == JsonValueKind.False)
                        result.Add(new ParcelReference { Category = "设计点间距", UnitId = unit });
                    if (row.TryGetProperty("覆盖缺失图斑", out var missing))
                        foreach (var parcel in missing.EnumerateArray()) result.Add(new ParcelReference { Category = "图斑缺采样点", UnitId = unit, ParcelId = parcel.ToString() });
                }
            if (report.TryGetProperty("影像逐单元", out var images))
                foreach (var row in images.EnumerateObject())
                    if (Value(row.Value, "coverage_status") != "PASS")
                        result.Add(new ParcelReference { Category = "影像覆盖", UnitId = row.Name });
            return result;
        }
    }
}
