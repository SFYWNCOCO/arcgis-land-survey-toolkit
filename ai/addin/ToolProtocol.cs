using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace ProAiBridge
{
    internal static class ToolProtocol
    {
        internal static readonly JsonArray Catalog = JsonNode.Parse(File.ReadAllText(
            Path.Combine(Path.GetDirectoryName(typeof(ToolProtocol).Assembly.Location), "tools.json"))).AsArray();

        internal static JsonObject Tool(string name) => Catalog.OfType<JsonObject>().SingleOrDefault(
            t => (string)t["name"] == name) ?? throw new ArgumentException("未知工具：" + name);

        internal static bool RequiresMap(string name) => (string)Tool(name)["_meta"]?["pro-ai-bridge/context"] != "project";

        /// <summary>
        /// 启动时校验工具目录完整性：名唯一、schema 存在、context 与 readOnlyHint 齐备。
        /// MCP 侧（Python jsonschema）与 C# 侧共用同一份 tools.json，这里拒绝半可用目录。
        /// </summary>
        internal static void ValidateCatalog()
        {
            if (Catalog.Count == 0) throw new InvalidOperationException("工具目录为空：tools.json");
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (var node in Catalog)
            {
                if (node is not JsonObject tool) throw new InvalidOperationException("工具目录存在非对象项。");
                var name = (string)tool["name"];
                if (string.IsNullOrWhiteSpace(name)) throw new InvalidOperationException("工具目录存在缺少 name 的项。");
                if (name.Length > 128 || !Regex.IsMatch(name, "^[a-z][a-z0-9_]*$"))
                    throw new InvalidOperationException("工具名不合规：" + name);
                if (!names.Add(name)) throw new InvalidOperationException("工具名重复：" + name);
                if (tool["description"] is null || (string)tool["description"] is null || string.IsNullOrWhiteSpace((string)tool["description"])
                    || tool["inputSchema"] is not JsonObject)
                    throw new InvalidOperationException("工具缺少 description 或 inputSchema：" + name);
                var declaredType = (string)((JsonObject)tool["inputSchema"])["type"];
                if (declaredType != "object")
                    throw new InvalidOperationException("工具 inputSchema 顶层必须为 object：" + name);
                var context = (string)tool["_meta"]?["pro-ai-bridge/context"];
                if (context != "map" && context != "project")
                    throw new InvalidOperationException("工具缺少合法 context：" + name);
                if (tool["annotations"]?["readOnlyHint"] is not JsonValue readOnly || !readOnly.TryGetValue<bool>(out _))
                    throw new InvalidOperationException("工具缺少 readOnlyHint：" + name);
            }
        }

        internal static void Validate(string name, JsonObject args)
        {
            if (args == null) throw new ArgumentException("arguments 必须为 JSON 对象。");
            ValidateValue(args, Tool(name)["inputSchema"].AsObject(), "arguments");
        }

        private static bool IsIntegral(JsonNode value)
        {
            var element = JsonSerializer.SerializeToElement(value);
            if (element.TryGetInt64(out _)) return true;
            return element.TryGetDouble(out var number) && Math.Abs(number - Math.Round(number)) < 1e-9;
        }

        private static void ValidateValue(JsonNode value, JsonObject schema, string path)
        {
            var kind = JsonSerializer.SerializeToElement(value).ValueKind;
            var type = (string)schema["type"];
            bool valid = type switch {
                "object" => kind == JsonValueKind.Object,
                "array" => kind == JsonValueKind.Array,
                "string" => kind == JsonValueKind.String,
                "boolean" => kind == JsonValueKind.True || kind == JsonValueKind.False,
                "number" => kind == JsonValueKind.Number,
                // 模型常把整数写成 10.0；整值浮点按整数接受（与 Python jsonschema 一致）
                "integer" => kind == JsonValueKind.Number && IsIntegral(value),
                _ => false
            };
            if (!valid) throw new ArgumentException(path + " 类型应为 " + type);
            if (schema["enum"] is JsonArray allowed && !allowed.Any(item => item.ToJsonString() == value.ToJsonString()))
                throw new ArgumentException(path + " 不在允许值中。");
            if (value is JsonObject obj)
            {
                var properties = schema["properties"].AsObject();
                foreach (var key in schema["required"] as JsonArray ?? new JsonArray())
                    if (!obj.ContainsKey((string)key)) throw new ArgumentException("缺少参数：" + key);
                foreach (var pair in obj)
                {
                    if (!properties.ContainsKey(pair.Key)) throw new ArgumentException("未知参数：" + pair.Key);
                    ValidateValue(pair.Value, properties[pair.Key].AsObject(), path + "." + pair.Key);
                }
            }
            else if (value is JsonArray array)
            {
                if (array.Count < (int?)schema["minItems"] || array.Count > (int?)schema["maxItems"])
                    throw new ArgumentException(path + " 数组长度越界。");
                foreach (var item in array) ValidateValue(item, schema["items"].AsObject(), path + "[]");
            }
            else if (type == "string")
            {
                var s = (string)value;
                if (s.Length < (int?)schema["minLength"] || s.Length > (int?)schema["maxLength"] || s.IndexOf('\0') >= 0)
                    throw new ArgumentException(path + " 字符串长度越界或含空字符。");
                if (schema["pattern"] != null && !Regex.IsMatch(s, (string)schema["pattern"]))
                    throw new ArgumentException(path + " 格式错误。");
            }
            else if (type == "number" || type == "integer")
            {
                var n = JsonSerializer.SerializeToElement(value).GetDouble();
                if (!double.IsFinite(n) || n < (double?)schema["minimum"] || n > (double?)schema["maximum"])
                    throw new ArgumentException(path + " 数值越界。");
            }
        }

        internal static JsonNode Copy(JsonNode node) => node == null ? null : JsonNode.Parse(node.ToJsonString());
        internal static string String(JsonObject args, string key, string fallback = "") => (string)args[key] ?? fallback;
        internal static int Int(JsonObject args, string key, int fallback)
        {
            var node = args?[key];
            if (node == null) return fallback;
            var element = JsonSerializer.SerializeToElement(node);
            if (element.TryGetInt32(out var direct)) return direct;
            // 模型常把整数写成 10.0；整值浮点按整数接受，并与 ValidateValue 的 integer 判定一致
            if (element.TryGetDouble(out var number) && Math.Abs(number - Math.Round(number)) < 1e-9
                && number >= int.MinValue && number <= int.MaxValue)
                return (int)Math.Round(number);
            throw new ArgumentException("参数 " + key + " 必须为整数。");
        }
    }
}
