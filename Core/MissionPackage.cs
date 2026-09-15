using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace RDR1AnimationStudio.Core;

/// <summary>
/// Writes a reproducible, single-resource mission package.  This is deliberately
/// an evidence package: fields that are not decoded are named as unresolved in
/// the manifest instead of being presented as guessed VM semantics.
/// </summary>
internal static class MissionPackage
{
    public static string Write(
        string outputDirectory,
        byte[] resource,
        string name,
        string archivePath,
        string internalPath,
        string? semanticsReportPath = null,
        byte[]? expandedPayload = null)
    {
        Directory.CreateDirectory(outputDirectory);
        string sourceName = SanitizeFileName(Path.GetFileName(name));
        if (string.IsNullOrWhiteSpace(sourceName)) sourceName = "mission.wsc";
        string sourcePath = Path.Combine(outputDirectory, sourceName);
        File.WriteAllBytes(sourcePath, resource);

        int payloadOffset = IsRsc(resource) ? (resource.Length >= 5 && (resource[4] == 0x05 || resource[4] == 0x06) ? 12 : 16) : 0;
        payloadOffset = Math.Clamp(payloadOffset, 0, resource.Length);
        byte[] payload = resource[payloadOffset..];
        string payloadPath = Path.Combine(outputDirectory, "payload.bin");
        File.WriteAllBytes(payloadPath, payload);
        string hexPath = Path.Combine(outputDirectory, "payload.hex.txt");
        File.WriteAllText(hexPath, FormatHexPreview(payload, 1024), Encoding.UTF8);
        string? decryptedPayloadPath = null;
        string? decryptedHexPath = null;
        if (Rsc85Crypto.IsEncryptedResource(resource))
        {
            byte[] decryptedPayload = Rsc85Crypto.DecryptPayload(payload);
            decryptedPayloadPath = Path.Combine(outputDirectory, "payload.decrypted.bin");
            decryptedHexPath = Path.Combine(outputDirectory, "payload.decrypted.hex.txt");
            File.WriteAllBytes(decryptedPayloadPath, decryptedPayload);
            File.WriteAllText(decryptedHexPath, FormatHexPreview(decryptedPayload, 1024), Encoding.UTF8);
        }
        string? expandedPayloadPath = null;
        string? expandedHexPath = null;
        if (expandedPayload is { Length: > 0 })
        {
            expandedPayloadPath = Path.Combine(outputDirectory, "payload.expanded.bin");
            expandedHexPath = Path.Combine(outputDirectory, "payload.expanded.hex.txt");
            File.WriteAllBytes(expandedPayloadPath, expandedPayload);
            File.WriteAllText(expandedHexPath, FormatHexPreview(expandedPayload, 1024), Encoding.UTF8);
        }

        string reportPath = Path.Combine(outputDirectory, "mission.semantics.json");
        if (!string.IsNullOrWhiteSpace(semanticsReportPath) && File.Exists(semanticsReportPath))
            File.Copy(semanticsReportPath, reportPath, true);
        else
            File.WriteAllText(reportPath, ResourceSemantics.DecodeToJson(resource, internalPath) + Environment.NewLine, Encoding.UTF8);
        string? traceFileName = WriteMissionTrace(reportPath, outputDirectory);
        string probePath = Rsc85Probe.Write(outputDirectory, resource);

        var manifest = new Dictionary<string, object?>
        {
            ["format"] = "RDR1 Animation Studio single mission package",
            ["schemaVersion"] = 3,
            ["decodeStatus"] = Rsc85Crypto.IsEncryptedResource(resource)
                ? expandedPayload is { Length: > 0 }
                    ? "RSC85 v2 AES payload transform, Zstandard expansion, VM header, opcode stream, embedded function boundaries, bounded Call2 graph, source-order mission trace, and typed resource correlation decoded"
                    : "RSC85 v2 AES payload transform validated; run the bundled Python decoder to expand Zstandard and inspect the VM header"
                : IsRsc(resource)
                    ? "RSC container validated; payload compression/resource graph remains unresolved"
                : "resource bytes preserved; container is not a recognized RSC mission resource",
            ["source"] = new Dictionary<string, object?>
            {
                ["name"] = name,
                ["archive"] = archivePath,
                ["internalPath"] = internalPath,
                ["byteLength"] = resource.Length,
                ["sha256"] = Convert.ToHexString(SHA256.HashData(resource)),
                ["file"] = Path.GetFileName(sourcePath)
            },
            ["rscHeader"] = ReadHeader(resource),
            ["payload"] = new Dictionary<string, object?>
            {
                ["offset"] = payloadOffset,
                ["byteLength"] = payload.Length,
                ["sha256"] = Convert.ToHexString(SHA256.HashData(payload)),
                ["file"] = Path.GetFileName(payloadPath),
                ["hexPreviewFile"] = Path.GetFileName(hexPath),
                ["decryptedFile"] = decryptedPayloadPath is null ? null : Path.GetFileName(decryptedPayloadPath),
                ["decryptedHexPreviewFile"] = decryptedHexPath is null ? null : Path.GetFileName(decryptedHexPath),
                ["decryptedSha256"] = decryptedPayloadPath is null ? null : Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(decryptedPayloadPath))),
                ["expandedFile"] = expandedPayloadPath is null ? null : Path.GetFileName(expandedPayloadPath),
                ["expandedHexPreviewFile"] = expandedHexPath is null ? null : Path.GetFileName(expandedHexPath),
                ["expandedSha256"] = expandedPayload is null ? null : Convert.ToHexString(SHA256.HashData(expandedPayload)),
                ["expandedByteLength"] = expandedPayload?.Length,
            },
            ["semanticReport"] = Path.GetFileName(reportPath),
            ["missionTrace"] = traceFileName,
            ["rscProbe"] = Path.GetFileName(probePath),
            ["unresolved"] = new[]
            {
                "RSC85/RSC86 physical page/resource graph mapping",
                "mission VM event/control-flow semantics beyond the validated bounded instruction stream",
                "event timing, camera transforms, AI arguments, and audio codec fields"
            },
            ["nextDecoderStep"] = "Validate typed record candidates against independently decoded event, camera, AI, prop, and audio resource streams; runtime branch conditions remain explicit."
        };
        string manifestPath = Path.Combine(outputDirectory, "mission.manifest.json");
        File.WriteAllText(manifestPath, JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true }) + Environment.NewLine, Encoding.UTF8);
        return manifestPath;
    }

    private static string? WriteMissionTrace(string reportPath, string outputDirectory)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(reportPath, Encoding.UTF8));
            JsonElement trace = default;
            if (document.RootElement.TryGetProperty("payload", out JsonElement payload) &&
                payload.TryGetProperty("missionTrace", out JsonElement payloadTrace))
                trace = payloadTrace;
            else if (document.RootElement.TryGetProperty("missionTrace", out JsonElement rootTrace))
                trace = rootTrace;
            if (trace.ValueKind != JsonValueKind.Object) return null;
            string tracePath = Path.Combine(outputDirectory, "mission.trace.json");
            File.WriteAllText(tracePath, JsonSerializer.Serialize(trace, new JsonSerializerOptions { WriteIndented = true }) + Environment.NewLine, Encoding.UTF8);
            return Path.GetFileName(tracePath);
        }
        catch (Exception)
        {
            // A fallback/native report may not contain a trace, and very large
            // reports should not make package creation fail after the source
            // artifacts have already been preserved.
            return null;
        }
    }

    private static bool IsRsc(byte[] data) => data.Length >= 4 && Encoding.ASCII.GetString(data, 0, 3) == "RSC";

    private static Dictionary<string, object?> ReadHeader(byte[] data)
    {
        if (!IsRsc(data)) return new() { ["present"] = false };
        return new()
        {
            ["present"] = data.Length >= 16,
            ["magic"] = Encoding.Latin1.GetString(data, 0, Math.Min(4, data.Length)),
            ["versionWord"] = ReadUInt32(data, 4),
            ["systemFlags"] = ReadUInt32(data, 8),
            ["systemFlagsHex"] = ReadUInt32(data, 8) is uint systemFlags ? $"0x{systemFlags:X8}" : null,
            ["graphicsFlags"] = ReadUInt32(data, 12),
            ["graphicsFlagsHex"] = ReadUInt32(data, 12) is uint graphicsFlags ? $"0x{graphicsFlags:X8}" : null,
            ["headerLength"] = data.Length >= 5 && (data[4] == 0x05 || data[4] == 0x06) ? 12 : 16
        };
    }

    private static uint? ReadUInt32(byte[] data, int offset) => offset + 4 <= data.Length ? BitConverter.ToUInt32(data, offset) : null;

    private static string FormatHexPreview(byte[] data, int maxBytes)
    {
        int count = Math.Min(data.Length, maxBytes);
        var builder = new StringBuilder(count * 3 + count / 16 * 20);
        for (int offset = 0; offset < count; offset += 16)
        {
            int row = Math.Min(16, count - offset);
            builder.Append(offset.ToString("X8")).Append("  ");
            for (int i = 0; i < 16; i++) builder.Append(i < row ? data[offset + i].ToString("X2") : "  ").Append(i == 7 ? "  " : " ");
            builder.Append(" ");
            for (int i = 0; i < row; i++) builder.Append(data[offset + i] is >= 32 and <= 126 ? (char)data[offset + i] : '.');
            builder.AppendLine();
        }
        if (data.Length > count) builder.AppendLine($"… truncated; {data.Length - count:N0} more bytes");
        return builder.ToString();
    }

    private static string SanitizeFileName(string value)
    {
        foreach (char invalid in Path.GetInvalidFileNameChars()) value = value.Replace(invalid, '_');
        return value;
    }
}
