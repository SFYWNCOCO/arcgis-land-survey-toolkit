using System.Text.Json;

namespace AcceptanceAddin
{
    internal static class LiveMcpProtocol
    {
        internal static bool IsZoomConfirmed(JsonElement request)
            => request.ValueKind == JsonValueKind.Object
                && request.TryGetProperty("confirm", out var value)
                && value.ValueKind == JsonValueKind.True;
    }
}
