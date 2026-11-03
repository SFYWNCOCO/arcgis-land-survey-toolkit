using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using ProAiBridge;

internal static class CoreHarness
{
    static async Task<int> Main(string[] args)
    {
        try
        {
            if (args[0] == "workbench_gate")
            {
                var first = AcceptanceAddin.WorkbenchJobService.Begin("sampling", "D:/result");
                bool blocked = false;
                try { using var other = AcceptanceAddin.WorkbenchJobService.Begin("gp", ""); }
                catch (InvalidOperationException) { blocked = true; }
                first.RequestStop(); first.Finish("Cancelled"); first.Dispose();
                using var second = AcceptanceAddin.WorkbenchJobService.Begin("gp", "");
                first.Finish("Succeeded"); first.Dispose();
                Console.WriteLine(new JsonObject { ["exclusive"] = blocked,
                    ["cancelled"] = first.Status == "Cancelled", ["released"] = second.Id != first.Id,
                    ["late_safe"] = (string)AcceptanceAddin.WorkbenchJobService.Snapshot()["id"] == second.Id }.ToJsonString());
            }
            else if (args[0] == "workbench_receipt")
            {
                Console.WriteLine(AcceptanceAddin.WorkbenchTaskReceipt.Read(args[1], args.Length > 2 ? args[2] : null).ToJsonString());
            }
            else if (args[0] == "workbench_delivery")
            {
                Console.WriteLine(AcceptanceAddin.AcceptanceDelivery.Read(args[1]).ToJsonString());
            }
            else if (args[0] == "workbench_error")
            {
                Console.WriteLine(AcceptanceAddin.WorkbenchDiagnostics.Error(new System.ComponentModel.Win32Exception(2), args[1]).ToJsonString());
            }
            else if (args[0] == "workbench_http")
            {
                var error = new System.Net.Http.HttpRequestException("HTTP response", null, (System.Net.HttpStatusCode)int.Parse(args[1]));
                Console.WriteLine(AcceptanceAddin.WorkbenchDiagnostics.Error(error, "map").ToJsonString());
            }
            else if (args[0] == "workbench_redact")
            {
                Console.WriteLine(new JsonObject { ["text"] = AcceptanceAddin.WorkbenchDiagnostics.Redact(args[1], new[] { "RAW_SECRET" }) }.ToJsonString());
            }
            else if (args[0] == "dataset_path")
            {
                Console.WriteLine(new JsonObject { ["path"] = PaneState.DatasetPath(args[1]) }.ToJsonString());
            }
            else if (args[0] == "scope_snapshot")
            {
                PaneState.EnsureScopeUnchanged(System.Text.Json.JsonSerializer.Deserialize<long[]>(args[1]),
                    System.Text.Json.JsonSerializer.Deserialize<long[]>(args[2]), args[3], args[4], args[5]);
                Console.WriteLine("{\"ok\":true}");
            }
            else if (args[0] == "basemap_merge")
            {
                BasemapPreview.SaveChanges(amapKey:"original-js", amapSecurityCode:"code", path:args[1]);
                BasemapPreview.SaveChanges(tiandituKey:"new-tdt", path:args[1]);
                BasemapPreview.SaveChanges(amapKey:"changed-js", path:args[1]);
                var value = BasemapPreview.Load(args[1]);
                if (value.TiandituKey != "new-tdt" || value.AmapKey != "changed-js" || value.AmapSecurityCode != "code") throw new Exception("共享字段被旧值覆盖");
                BasemapPreview.SaveChanges(tiandituKey:"", path:args[1]);
                Console.WriteLine(new JsonObject { ["preserved"] = true, ["explicit_clear"] = BasemapPreview.Load(args[1]).TiandituKey == "" }.ToJsonString());
            }
            else if (args[0] == "command")
                Console.WriteLine(new JsonObject { ["command"] = PaneState.ParseCommand(args[1]) }.ToJsonString());
            else if (args[0] == "basemap_catalog")
                Console.WriteLine(args.Length > 1 && args[1] == "ready"
                    ? BasemapPreview.Catalog(new BasemapSettings { AmapKey = "TEST_JS_KEY", AmapSecurityCode = "TEST_JS_CODE",
                        TiandituKey = "TEST_TDT_KEY" }, true).ToJsonString()
                    : BasemapPreview.Catalog(new BasemapSettings()).ToJsonString());
            else if (args[0] == "basemap_preview_sources")
                Console.WriteLine(new JsonObject {
                    ["sources"] = new JsonArray(BasemapPreview.Sources.Select(name => (JsonNode)JsonValue.Create(name)).ToArray()),
                    ["preview_sources"] = new JsonArray(BasemapPreview.PreviewSources.Select(name => (JsonNode)JsonValue.Create(name)).ToArray()),
                    ["labels"] = new JsonArray(BasemapPreview.Sources.Select(name => (JsonNode)JsonValue.Create(BasemapPreview.Label(name))).ToArray()),
                    ["corrections"] = new JsonArray(BasemapPreview.Sources.Select(name =>
                        (JsonNode)JsonValue.Create(BasemapPreview.CorrectionLabel(name))).ToArray()),
                    ["gcj02"] = new JsonArray(BasemapPreview.Sources.Select(name =>
                        (JsonNode)JsonValue.Create(BasemapPreview.IsGcj02(name))).ToArray()),
                    ["tianditu"] = new JsonArray(BasemapPreview.Sources.Select(name =>
                        (JsonNode)JsonValue.Create(BasemapPreview.IsTianditu(name))).ToArray()) }.ToJsonString());
            else if (args[0] == "snapshot_cache_key")
            {
                var extent = new[] {
                    double.Parse(args[2], System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(args[3], System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(args[4], System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(args[5], System.Globalization.CultureInfo.InvariantCulture) };
                Console.WriteLine(new JsonObject { ["key"] = BasemapPreview.SnapshotCacheKey(
                    int.Parse(args[1], System.Globalization.CultureInfo.InvariantCulture), extent, args[6],
                    args.Length > 7 && args[7] == "true",
                    args.Length > 8 ? int.Parse(args[8], System.Globalization.CultureInfo.InvariantCulture)
                        : BasemapPreview.ExtentDecimals) }.ToJsonString());
            }
            else if (args[0] == "basemap_district_cache_key")
            {
                var extent = new[] {
                    double.Parse(args[1], System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(args[2], System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(args[3], System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(args[4], System.Globalization.CultureInfo.InvariantCulture) };
                var level = args.Length > 5 ? args[5] : "县乡";
                var keywords = args.Length > 6 ? args[6] : "";
                var keyHash = "ABCD1234|amap_district|" + level + "|" + keywords;
                Console.WriteLine(new JsonObject {
                    ["decimals"] = BasemapPreview.DistrictExtentDecimals,
                    ["default_decimals"] = BasemapPreview.ExtentDecimals,
                    ["key"] = BasemapPreview.SnapshotCacheKey(4525, extent, keyHash, false,
                        BasemapPreview.DistrictExtentDecimals),
                    ["default_key"] = BasemapPreview.SnapshotCacheKey(4525, extent, keyHash, false) }.ToJsonString());
            }
            else if (args[0] == "tool_profile")
            {
                var names = new JsonArray();
                foreach (var name in ModelClient.ToolNamesForPrompt(args[1])) names.Add(name);
                Console.WriteLine(new JsonObject { ["tools"] = names }.ToJsonString());
            }
            else if (args[0] == "tool_scope")
            {
                var scope = ModelClient.ExtendToolScope(ModelClient.ToolNamesForPrompt(args[1]), args[2]);
                Console.WriteLine(new JsonObject { ["tools"] = new JsonArray(scope.Select(name => (JsonNode)JsonValue.Create(name)).ToArray()) }.ToJsonString());
            }
            else if (args[0] == "complete_pending_tools")
            {
                var messages = System.Text.Json.JsonSerializer.Deserialize<List<JsonNode>>(args[1]);
                ModelClient.CompletePendingToolCalls(messages);
                Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(messages));
            }
            else if (args[0] == "basemap_page")
            {
                using var preview = BasemapPreview.Start(args[1], double.Parse(args[2], System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(args[3], System.Globalization.CultureInfo.InvariantCulture),
                    new BasemapSettings { AmapKey = "TEST_JS_KEY", AmapSecurityCode = "TEST_JS_CODE", TiandituKey = "TEST_TDT_KEY" });
                using var http = new System.Net.Http.HttpClient();
                var response = await http.GetAsync(preview.Url);
                var body = await response.Content.ReadAsStringAsync();
                var denied = await http.GetAsync(preview.Url.Replace(preview.Token, "invalid"));
                using var crossOrigin = new System.Net.Http.HttpRequestMessage(System.Net.Http.HttpMethod.Get, preview.Url);
                crossOrigin.Headers.TryAddWithoutValidation("Origin", "https://untrusted.example");
                using var crossOriginResponse = await http.SendAsync(crossOrigin);
                Console.WriteLine(new JsonObject { ["status"] = (int)response.StatusCode, ["denied"] = (int)denied.StatusCode,
                    ["origin_denied"] = (int)crossOriginResponse.StatusCode, ["cache"] = response.Headers.CacheControl.ToString(),
                    ["html"] = body, ["url"] = preview.Url }.ToJsonString());
            }
            else if (args[0] == "basemap_tile_route")
            {
                var matched = BasemapPreview.TryParseTileRoute(args[1], args[2], args[3], out var layer, out var zoom, out var row, out var column);
                Console.WriteLine(new JsonObject { ["matched"] = matched, ["layer"] = matched ? layer : null,
                    ["zoom"] = matched ? zoom : 0, ["row"] = matched ? row : 0, ["column"] = matched ? column : 0 }.ToJsonString());
            }
            else if (args[0] == "basemap_missing")
            {
                using var preview = BasemapPreview.Start(args[1], 106.9, 23.7, new BasemapSettings());
            }
            else if (args[0] == "basemap_map_template")
            {
                var proxy = BasemapPreview.StartMap("test-map", args[1], new BasemapSettings { TiandituKey = "TEST_TDT_KEY" });
                try { Console.WriteLine(new JsonObject { ["template"] = proxy.MapTemplate, ["labels"] = proxy.LabelTemplate, ["url"] = proxy.Url }.ToJsonString()); }
                finally { BasemapPreview.CloseMap("test-map"); }
            }
            else if (args[0] == "basemap_map_isolation")
            {
                var first = BasemapPreview.StartMap("map-a", "tianditu_image", new BasemapSettings { TiandituKey = "TEST_TDT_KEY" });
                var second = BasemapPreview.StartMap("map-b", "tianditu_image", new BasemapSettings { TiandituKey = "TEST_TDT_KEY" });
                try
                {
                    using var http = new System.Net.Http.HttpClient();
                    var a = await http.GetAsync(first.Url); var b = await http.GetAsync(second.Url);
                    BasemapPreview.CloseMap("map-b");
                    var aAfter = await http.GetAsync(first.Url);
                    Console.WriteLine(new JsonObject { ["a"] = (int)a.StatusCode, ["b"] = (int)b.StatusCode,
                        ["a_after_close_b"] = (int)aAfter.StatusCode }.ToJsonString());
                }
                finally { BasemapPreview.CloseAllMaps(); }
            }
            else if (args[0] == "basemap_map_missing")
            {
                using var proxy = BasemapPreview.StartMap("test-map", args[1], new BasemapSettings());
            }
            else if (args[0] == "basemap_map_tile")
            {
                var proxy = BasemapPreview.StartMap("test-map", args[1], BasemapPreview.Load());
                try
                {
                    using var http = new System.Net.Http.HttpClient();
                    var response = await http.GetAsync(proxy.Url + "/xyz/img/18/113304/208669.jpg");
                    var body = await response.Content.ReadAsByteArrayAsync();
                    if ((int)response.StatusCode == 200)
                        System.IO.File.WriteAllBytes(System.IO.Path.Combine(System.IO.Path.GetTempPath(), "tianditu_tile.png"), body);
                    Console.WriteLine(new JsonObject { ["status"] = (int)response.StatusCode, ["bytes"] = body.Length,
                        ["content_type"] = response.Content.Headers.ContentType?.MediaType ?? "",
                        ["body"] = (int)response.StatusCode == 200 ? "" : System.Text.Encoding.UTF8.GetString(body) }.ToJsonString());
                }
                finally { BasemapPreview.CloseMap("test-map"); }
            }
            else if (args[0] == "tile_type")
            {
                var png = new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 };
                var jpeg = new byte[] { 255, 216, 255, 0 };
                var bad = new byte[] { 1, 2, 3 };
                Console.WriteLine(new JsonObject { ["png"] = BasemapPreview.ImageTileType(png),
                    ["jpeg"] = BasemapPreview.ImageTileType(jpeg), ["bad"] = BasemapPreview.ImageTileType(bad) }.ToJsonString());
            }
            else if (args[0] == "tianditu_direct")
            {
                var settings = BasemapPreview.Load();
                if (string.IsNullOrWhiteSpace(settings.TiandituKey)) throw new ArgumentException("天地图 Key 未配置。");
                var uri = "https://t0.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img" +
                    "&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX=18&TILEROW=113304&TILECOL=208669&tk=" +
                    Uri.EscapeDataString(settings.TiandituKey);
                using var handler = new System.Net.Http.HttpClientHandler { AllowAutoRedirect = false, UseProxy = false };
                using var http = new System.Net.Http.HttpClient(handler) { Timeout = TimeSpan.FromSeconds(25) };
                try
                {
                    var response = await http.GetAsync(uri);
                    var body = await response.Content.ReadAsByteArrayAsync();
                    Console.WriteLine(new JsonObject { ["status"] = (int)response.StatusCode, ["bytes"] = body.Length,
                        ["content_type"] = response.Content.Headers.ContentType?.MediaType ?? "" }.ToJsonString());
                }
                catch (Exception ex)
                {
                    Console.WriteLine(new JsonObject { ["status"] = -1, ["error"] = ex.GetType().Name }.ToJsonString());
                }
            }
            else if (args[0] == "tianditu_upstream")
            {
                var settings = BasemapPreview.Load();
                if (string.IsNullOrWhiteSpace(settings.TiandituKey)) throw new ArgumentException("天地图 Key 未配置。");
                var uri = "https://t0.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img" +
                    "&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX=18&TILEROW=113304&TILECOL=208669&tk=" +
                    Uri.EscapeDataString(settings.TiandituKey);
                using var handler = new System.Net.Http.HttpClientHandler { AllowAutoRedirect = false };
                using var http = new System.Net.Http.HttpClient(handler);
                var response = await http.GetAsync(uri);
                var body = await response.Content.ReadAsByteArrayAsync();
                Console.WriteLine(new JsonObject { ["status"] = (int)response.StatusCode, ["bytes"] = body.Length,
                    ["content_type"] = response.Content.Headers.ContentType?.MediaType ?? "",
                    ["location"] = response.Headers.Location?.ToString() ?? "",
                    ["head"] = body.Length >= 4 ? string.Format("{0:X2} {1:X2} {2:X2} {3:X2}", body[0], body[1], body[2], body[3]) : "" }.ToJsonString());
            }
            else if (args[0] == "basemap_settings")
            {
                BasemapPreview.Save(new BasemapSettings { AmapKey = "PRIVATE_JS_KEY", AmapSecurityCode = "PRIVATE_JS_CODE", TiandituKey = "PRIVATE_TDT_KEY" }, args[1]);
                var loaded = BasemapPreview.Load(args[1]);
                Console.WriteLine(new JsonObject { ["same"] = loaded.AmapKey == "PRIVATE_JS_KEY" && loaded.AmapSecurityCode == "PRIVATE_JS_CODE" && loaded.TiandituKey == "PRIVATE_TDT_KEY",
                    ["stored"] = System.IO.File.ReadAllText(args[1]) }.ToJsonString());
            }
            else if (args[0] == "source_match")
                Console.WriteLine(new JsonObject { ["matched"] = GpJob.SameFeatureSource(args[1], args[2]) }.ToJsonString());
            else if (args[0] == "job_cancel_callback")
            {
                using var job = new GpJob("management.GetCount", new[] { "D:/data.gdb/a" }, "D:/test.aprx");
                job.Set("running", "", "");
                using var registration = job.Token.Register(() => Task.Run(() => {
                    job.Set("cancelled", "", "callback"); job.Snapshot(); job.Dispose();
                }).GetAwaiter().GetResult());
                var cancelled = Task.Run(() => job.RequestCancel());
                if (await Task.WhenAny(cancelled, Task.Delay(1000)) != cancelled) throw new Exception("取消回调被状态锁阻塞");
                await cancelled;
                Console.WriteLine(job.Snapshot().ToJsonString());
            }
            else if (args[0] == "job")
            {
                var parameters = args[1] == "analysis.Clip" || args[1] == "analysis.SpatialJoin" ? new[] { "D:/data.gdb/input", "D:/data.gdb/clip", args[2] } : new[] { "D:/data.gdb/input", args[2] };
                using var job = new GpJob(args[1], parameters, "D:/test.aprx");
                var queued = job.Snapshot();
                job.Set("running", "", "");
                await Task.Delay(30);
                var running = job.Snapshot();
                job.Set(args[3], args.Length > 5 ? args[5] : "3", "receipt");
                var completed = job.Snapshot();
                await Task.Delay(20);
                job.RequestCancel();
                job.Set("cancelled", "", "late completion must not overwrite");
                string output = null; string error = null;
                try { output = job.RequireFeatureOutput(args.Length > 4 ? args[4] : "D:/test.aprx"); }
                catch (Exception ex) { error = ex.Message; }
                Console.WriteLine(new JsonObject { ["queued"] = queued, ["running"] = running,
                    ["completed"] = completed, ["after_cancel"] = job.Snapshot(), ["output"] = output, ["error"] = error }.ToJsonString());
            }
            else if (args[0] == "analysis")
            {
                var input = System.Text.Json.JsonSerializer.Deserialize<FeatureSource>(args[3]);
                var clip = args.Length > 4 ? System.Text.Json.JsonSerializer.Deserialize<FeatureSource>(args[4]) : null;
                var transforms = args.Length > 5 ? System.Text.Json.JsonSerializer.Deserialize<string[]>(args[5]) : null;
                var plan = AnalysisPlan.Prepare(args[1], JsonNode.Parse(args[2]).AsObject(), input, clip, transforms);
                Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(plan));
            }
            else if (args[0] == "order")
            {
                Console.WriteLine(new JsonObject { ["index"] = AnalysisPlan.MoveIndex(args[1].Split(','), args[2], args[3], args[4] == "after") }.ToJsonString());
            }
            else if (args[0] == "map")
            {
                PaneState.EnsureMap(args[1], args[2]);
                Console.WriteLine("{\"ok\":true}");
            }
            else if (args[0] == "validate")
            {
                ToolProtocol.Validate(args[1], JsonNode.Parse(args[2]).AsObject());
                Console.WriteLine("{\"ok\":true}");
            }
            else if (args[0] == "project")
            {
                PaneState.EnsureProject(args[1], args[2]);
                Console.WriteLine("{\"ok\":true}");
            }
            else if (args[0] == "gpparams")
            {
                var values = JsonNode.Parse(args[2]).AsArray();
                var resolved = PaneState.PrepareGpParameters(args[1], System.Linq.Enumerable.ToArray(System.Linq.Enumerable.Select(values, v => (string)v)),
                    input => input == "test_layer" ? "D:/test.gdb/points" : throw new ArgumentException("找不到图层"));
                Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(resolved));
            }
            else if (args[0] == "output")
            {
                var state = new PaneState();
                var accepted = state.RecordOutput(args[1], new JsonObject { ["path"] = args[2], ["bytes"] = 7 });
                Console.WriteLine(new JsonObject { ["accepted"] = accepted, ["path"] = state.OutputPath }.ToJsonString());
            }
            else if (args[0] == "job_cancel_available")
            {
                Console.WriteLine(new JsonObject { ["available"] = PaneState.CanCancelJob(JsonNode.Parse(args[1]), args[2]) }.ToJsonString());
            }
            else if (args[0] == "monitor")
            {
                var state = new PaneState();
                var delayed = new TaskCompletionSource<JsonNode>();
                var calls = 0;
                var first = state.RefreshJobsAsync(token => { calls++; return delayed.Task; }, CancellationToken.None);
                if (args.Length > 1 && args[1] == "reset")
                {
                    state.Reset();
                    delayed.SetResult(JsonNode.Parse("{\"jobs\":[{\"job_id\":\"old\",\"status\":\"succeeded\"}]}"));
                    await first;
                    Console.WriteLine(new JsonObject { ["count"] = state.Jobs.Count }.ToJsonString());
                    return 0;
                }
                var overlapped = await state.RefreshJobsAsync(token => { calls++; return delayed.Task; }, CancellationToken.None);
                delayed.SetResult(JsonNode.Parse("{\"jobs\":[{\"job_id\":\"one\",\"status\":\"running\"}]}"));
                var initial = await first;
                var repeated = await state.RefreshJobsAsync(token => Task.FromResult<JsonNode>(JsonNode.Parse("{\"jobs\":[{\"job_id\":\"one\",\"status\":\"running\"}]}")), CancellationToken.None);
                var completed = await state.RefreshJobsAsync(token => Task.FromResult<JsonNode>(JsonNode.Parse("{\"jobs\":[{\"job_id\":\"one\",\"status\":\"succeeded\",\"value\":\"3\"}]}")), CancellationToken.None);
                Console.WriteLine(new JsonObject { ["fetches_during_overlap"] = calls, ["overlapped"] = overlapped,
                    ["initial"] = initial, ["repeated"] = repeated, ["completed"] = completed, ["jobs"] = ToolProtocol.Copy(state.Jobs) }.ToJsonString());
            }
            else if (args[0] == "monitor_output")
            {
                var state = new PaneState();
                await state.RefreshJobsAsync(token => Task.FromResult<JsonNode>(new JsonObject { ["jobs"] = new JsonArray(),
                    ["latest_export"] = new JsonObject { ["tool"] = "export_map", ["path"] = args[1], ["bytes"] = 7 } }), CancellationToken.None);
                Console.WriteLine(new JsonObject { ["path"] = state.OutputPath }.ToJsonString());
            }
            else if (args[0] == "diagnostics")
                Console.WriteLine(PaneState.Diagnose(args[1], true, 123, "D:/test.aprx").ToJsonString());
            else if (args[0] == "review_saved")
            {
                if (args.Length < 4 || string.IsNullOrWhiteSpace(args[2]) ||
                    !System.IO.Path.IsPathFullyQualified(args[3]) ||
                    !string.Equals(System.IO.Path.GetFullPath(args[3]), System.IO.Path.GetFullPath(args[1]) + ".visual-review.json", StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("请提供图件、当前render_key和该图件旁的.visual-review.json绝对路径。");
                static string ImageHash(string path)
                {
                    using var image = System.IO.File.OpenRead(path);
                    using var sha = System.Security.Cryptography.SHA256.Create();
                    return Convert.ToHexString(sha.ComputeHash(image)).ToLowerInvariant();
                }
                var before = ImageHash(args[1]);
                var context = args.Length > 4 ? System.IO.File.ReadAllText(args[4]) : "逐项检查图件版面、点号与边界、图例、比例尺、网格和影像；不确定内容保留待核。";
                var review = await new ModelClient().ReviewMapAsync(SettingsStore.Load(args.Length > 5 ? args[5] : null), args[1], context, CancellationToken.None);
                if ((string)review["image_sha256"] != before || ImageHash(args[1]) != before) throw new InvalidOperationException("图件已变更，未写入视觉回执。");
                review["render_key"] = args[2];
                var temporary = args[3] + ".tmp." + Guid.NewGuid().ToString("N");
                try
                {
                    System.IO.File.WriteAllText(temporary, review.ToJsonString(), new System.Text.UTF8Encoding(false));
                    if (ImageHash(args[1]) != before) throw new InvalidOperationException("写回前图件已变更。");
                    System.IO.File.Move(temporary, args[3], true);
                    if (ImageHash(args[1]) != before) throw new InvalidOperationException("写回后图件已变更，回执无效。");
                }
                finally { if (System.IO.File.Exists(temporary)) System.IO.File.Delete(temporary); }
                Console.WriteLine(new JsonObject { ["status"] = (string)review["status"], ["receipt_path"] = args[3],
                    ["model"] = (string)review["model"], ["image_sha256"] = before }.ToJsonString());
            }
            else if (args[0] == "review_map")
            {
                using var cancellation = new CancellationTokenSource();
                if (args.Length > 3 && args[3] == "cancel") cancellation.CancelAfter(100);
                var review = await new ModelClient().ReviewMapAsync(
                    new ModelSettings { BaseUrl = args[1], Model = "vision-test", ApiKey = "secret-test" },
                    args[2], "检查采样点、边界、图例、比例尺和文字遮挡。", cancellation.Token);
                Console.WriteLine(review.ToJsonString());
            }
            else if (args[0] == "endpoint")
            {
                var settings = new ModelSettings { BaseUrl = args[1], Model = "test", ApiKey = "secret-test" };
                Console.WriteLine(new JsonObject { ["url"] = settings.Endpoint().ToString() }.ToJsonString());
            }
            else if (args[0] == "models")
            {
                using var cancellation = new CancellationTokenSource();
                if (args.Length > 2 && args[2] == "cancel") cancellation.CancelAfter(100);
                var models = await new ModelClient().ListModelsAsync(new ModelSettings { BaseUrl = args[1], ApiKey = "secret-test" }, cancellation.Token);
                Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(models));
            }
            else if (args[0] == "timeout_setting")
            {
                var settings = new ModelSettings { BaseUrl = "http://127.0.0.1/v1", Model = "test", RequestTimeoutSeconds = int.Parse(args[1]) };
                settings.Endpoint(); Console.WriteLine("{\"ok\":true}");
            }
            else if (args[0] == "load_settings")
            {
                var settings = SettingsStore.Load(args[1]);
                Console.WriteLine(new JsonObject { ["timeout"] = settings.RequestTimeoutSeconds, ["models_url"] = settings.ModelsEndpoint().AbsoluteUri }.ToJsonString());
            }
            else if (args[0] == "settings")
            {
                var settings = new ModelSettings { BaseUrl = "http://127.0.0.1:11434/v1", Model = "test", ApiKey = "secret-test", RequestTimeoutSeconds = 17 };
                SettingsStore.Save(settings, args[1]);
                var loaded = SettingsStore.Load(args[1]);
                Console.WriteLine(new JsonObject { ["roundtrip"] = loaded.ApiKey == settings.ApiKey, ["timeout"] = loaded.RequestTimeoutSeconds }.ToJsonString());
            }
            else if (args[0] == "cancel_boundary")
            {
                using var cancelled = new CancellationTokenSource();
                var executed = 0;
                try
                {
                    await new ModelClient().RunAsync(new ModelSettings { BaseUrl = args[1], Model = "test" }, "测试取消边界",
                        (name, input, token) => { executed++; return Task.FromResult<JsonNode>(new JsonObject()); },
                        (name, input) => { cancelled.Cancel(); return Task.FromResult(true); },
                        message => { if (args[2] == "usage" && message.Contains("tokens")) cancelled.Cancel(); }, cancelled.Token);
                    Console.WriteLine(new JsonObject { ["cancelled"] = false, ["executed"] = executed }.ToJsonString());
                }
                catch (OperationCanceledException)
                {
                    Console.WriteLine(new JsonObject { ["cancelled"] = true, ["executed"] = executed }.ToJsonString());
                }
            }
            else if (args[0] == "model" || args[0] == "connection")
            {
                var executed = new JsonArray(); var confirmations = new JsonArray();
                using var cancel = new CancellationTokenSource();
                if (args.Length > 3 && args[3] == "cancel") cancel.CancelAfter(150);
                var client = new ModelClient();
                var traces = new JsonArray();
                var receipts = new JsonArray();
                string reply;
                try
                {
                reply = await client.RunAsync(new ModelSettings { BaseUrl = args[1], Model = "test-model", ApiKey = "secret-test", RequestTimeoutSeconds = args.Length > 3 && args[3] == "short_timeout" ? 5 : 120 },
                    "列出图层", (name, input, token) => {
                        executed.Add(name);
                        return Task.FromResult<JsonNode>(new JsonObject { ["layers"] = new JsonArray("测试地块"),
                            ["padding"] = args.Length > 3 && args[3] == "huge" ? new string('x', 20000) : "" });
                    }, (name, input) => {
                        confirmations.Add(name);
                        return Task.FromResult(args.Length <= 2 || args[2] != "deny");
                    }, message => traces.Add(message), cancel.Token, args[0] != "connection",
                    (name, phase, detail) => receipts.Add(new JsonObject { ["name"] = name, ["phase"] = phase, ["detail"] = detail }));
                }
                catch (Exception ex)
                {
                    Console.WriteLine(new JsonObject { ["error"] = ex.Message, ["type"] = ex.GetType().Name, ["executed"] = executed, ["confirmations"] = confirmations }.ToJsonString());
                    return 1;
                }
                Console.WriteLine(new JsonObject { ["text"] = reply, ["executed"] = executed, ["confirmations"] = confirmations, ["traces"] = traces, ["receipts"] = receipts }.ToJsonString());
            }
            else if (args[0] == "twice")
            {
                var client = new ModelClient();
                var settings = new ModelSettings { BaseUrl = args[1], Model = "model-a" };
                Func<string, JsonObject, CancellationToken, Task<JsonNode>> execute = (name, input, token) => Task.FromResult<JsonNode>(new JsonObject());
                Func<string, JsonObject, Task<bool>> confirm = (name, input) => Task.FromResult(false);
                var cleared = false;
                await client.RunAsync(settings, "第一条私有会话", execute, confirm, _ => {
                    if (args[2] == "clear" && !cleared) { client.Clear(); cleared = true; }
                }, CancellationToken.None);
                if (args[2] == "switch") settings.Model = "model-b";
                await client.RunAsync(settings, "第二条新会话", execute, confirm, _ => {}, CancellationToken.None);
                Console.WriteLine("{\"ok\":true}");
            }
            else if (args[0] == "scope_twice")
            {
                var client = new ModelClient();
                var settings = new ModelSettings { BaseUrl = args[1], Model = "model-a" };
                Func<string, JsonObject, CancellationToken, Task<JsonNode>> execute = (name, input, token) => Task.FromResult<JsonNode>(new JsonObject());
                Func<string, JsonObject, Task<bool>> confirm = (name, input) => Task.FromResult(false);
                var cleared = false;
                await client.RunAsync(settings, "缓冲分析", execute, confirm, _ => {
                    if (args[2] == "clear" && !cleared) { client.Clear(); cleared = true; }
                }, CancellationToken.None);
                if (args[2] == "switch") settings.Model = "model-b";
                await client.RunAsync(settings, "继续", execute, confirm, _ => {}, CancellationToken.None);
                Console.WriteLine("{\"ok\":true}");
            }
            return 0;
        }
        catch (Exception ex)
        {
            Console.WriteLine(new JsonObject { ["error"] = ex.Message, ["type"] = ex.GetType().Name }.ToJsonString());
            return 1;
        }
    }
}
