using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Xml.Linq;

namespace RDR1AnimationStudio.Core;

/// <summary>Conservative semantic reader for the resource formats validated in game samples.</summary>
internal static class ResourceSemantics
{
    private static readonly HashSet<string> RscMagics = ["RSC\x05", "RSC\x06", "RSC\x85", "RSC\x86"];

    public static string DecodeToJson(byte[] data, string source)
    {
        var report = Decode(data);
        report["source"] = source;
        return JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true });
    }

    private static Dictionary<string, object?> Decode(byte[] data)
    {
        if (data.Length >= 4 && Encoding.ASCII.GetString(data, 0, 4) == "RBF0") return DecodeRbf(data);
        string magic = data.Length >= 4 ? Encoding.Latin1.GetString(data, 0, 4) : "";
        if (RscMagics.Contains(magic))
        {
            int headerLength = data.Length >= 5 && (data[4] == 0x05 || data[4] == 0x06) ? 12 : 16;
            byte[] payload = data[Math.Min(headerLength, data.Length)..];
            uint? version = ReadUInt32(data, 4);
            var report = new Dictionary<string, object?>
            {
                ["format"] = "RSC",
                ["confidence"] = "verified RSC wrapper; payload bytes preserved exactly",
                ["magic"] = magic,
                ["versionWord"] = version,
                ["systemFlags"] = ReadUInt32(data, 8),
                ["systemFlagsHex"] = ReadUInt32(data, 8) is uint systemFlags ? $"0x{systemFlags:X8}" : null,
                ["graphicsFlags"] = ReadUInt32(data, 12),
                ["graphicsFlagsHex"] = ReadUInt32(data, 12) is uint graphicsFlags ? $"0x{graphicsFlags:X8}" : null,
                ["headerLength"] = headerLength,
                ["actualSize"] = data.Length,
                ["payloadOffset"] = headerLength,
                ["payloadSize"] = payload.Length,
                ["expectedExpandedSegments"] = ExpandedSegments(data, magic),
                ["payloadSha256"] = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(payload)),
                ["payloadPrefixHex"] = Convert.ToHexString(payload[..Math.Min(payload.Length, 64)]),
            };
            if (magic is "RSC\x85" or "RSC\x86")
            {
                report["rsc85Layout"] = Rsc85Layout(data, magic);
                if (version == 2)
                {
                    try
                    {
                        byte[] transformed = Rsc85Crypto.DecryptPayload(payload);
                        report["payloadEncryption"] = Rsc85Crypto.Describe(payload);
                        report["transformedPayloadSha256"] = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(transformed));
                        report["transformedPayloadPrefixHex"] = Convert.ToHexString(transformed[..Math.Min(transformed.Length, 64)]);
                        report["unresolved"] = new[] { "Zstandard expansion is delegated to the bundled Python decoder when available", "mission VM header and opcode stream", "event timing, camera transforms, AI arguments, and audio codec fields" };
                    }
                    catch (Exception ex)
                    {
                        report["decryptionError"] = ex.Message;
                        report["unresolved"] = new[] { "RSC85 AES payload transform", "mission VM header and opcode stream", "event timing, camera transforms, AI arguments, and audio codec fields" };
                    }
                }
                else
                    report["unresolved"] = new[] { "RSC85 payload transform for this version", "mission VM header and opcode stream", "event timing, camera transforms, AI arguments, and audio codec fields" };
            }
            else
                report["unresolved"] = new[] { "RSC page/resource-graph compression or encryption", "mission VM header and opcode stream", "event timing, camera transforms, AI arguments, and audio codec fields" };
            return report;
        }
        if (data.Length >= 4 && data[0] == 0x28 && data[1] == 0xB5 && data[2] == 0x2F && data[3] == 0xFD)
            return new() { ["format"] = "Zstandard", ["confidence"] = "verified frame header", ["compressedSize"] = data.Length, ["unresolved"] = new[] { "install the optional Python zstandard module to expand this payload" } };
        string text = Encoding.UTF8.GetString(data).TrimStart('\uFEFF', ' ', '\t', '\r', '\n');
        if (text.StartsWith("<?xml", StringComparison.Ordinal) || text.StartsWith("<default", StringComparison.Ordinal)) return DecodeXml(text);
        return new() { ["format"] = "raw", ["confidence"] = "verified byte stream only", ["size"] = data.Length, ["strings"] = ExtractStrings(data), ["unresolved"] = new[] { "resource type/layout is not recognized" } };
    }

    private static Dictionary<string, object?> DecodeRbf(byte[] data)
    {
        int end = Array.IndexOf(data, (byte)0, 8); if (end < 0) end = Math.Min(data.Length, 256);
        string className = Encoding.ASCII.GetString(data, 8, Math.Max(0, end - 8));
        var labels = ExtractStrings(data);
        var groups = new Dictionary<string, List<object>>(StringComparer.OrdinalIgnoreCase);
        foreach (var label in labels)
        {
            string bucket = Bucket((string)label["value"]!);
            if (!groups.TryGetValue(bucket, out var list)) groups[bucket] = list = [];
            list.Add(label);
        }
        int objectTypes = labels.Count(label => ((string)label["value"]!).StartsWith("rage__cutf", StringComparison.Ordinal));
        int events = labels.Count(label => ((string)label["value"]!).StartsWith("rage__cutfObjectIdEvent9", StringComparison.Ordinal));
        var typedRecords = TypedRecordCandidates(labels);
        return new()
        {
            ["format"] = "RBF0", ["confidence"] = "verified container and labels; scalar encodings remain raw",
            ["class"] = className, ["declaredSize"] = data.Length >= 8 ? BitConverter.ToUInt32(data, 4) : null, ["actualSize"] = data.Length,
            ["semanticCounts"] = new Dictionary<string, int> { ["objectTypes"] = objectTypes, ["eventRecords"] = events, ["cameraLabels"] = Count(groups, "camera"), ["audioLabels"] = Count(groups, "audio"), ["effectLabels"] = Count(groups, "effect"), ["subtitleLabels"] = Count(groups, "subtitle"), ["actorLabels"] = Count(groups, "actor"), ["propLabels"] = Count(groups, "prop") },
            ["labels"] = groups,
            ["typedRecordCandidates"] = typedRecords,
            ["typedRecordCount"] = typedRecords.Count,
            ["unresolved"] = new[] { "numeric property payloads are preserved as bytes until the RBF value-tag table is validated", "event timing and camera transforms require decoding the typed record stream" }
        };
    }

    private static Dictionary<string, object?> DecodeXml(string text)
    {
        var root = XDocument.Parse(text).Root ?? throw new InvalidDataException("XML resource has no root element.");
        var elements = root.DescendantsAndSelf().Select(element => new Dictionary<string, object?> { ["tag"] = element.Name.LocalName, ["attributes"] = element.Attributes().ToDictionary(a => a.Name.LocalName, a => (object?)Typed(a.Value)) }).ToArray();
        var labels = elements.Select((element, index) => new Dictionary<string, object?> { ["offset"] = index, ["value"] = element["tag"] }).ToList();
        var typedRecords = TypedRecordCandidates(labels);
        return new() { ["format"] = "XML", ["confidence"] = "verified XML element and attribute values", ["root"] = root.Name.LocalName, ["elementCount"] = elements.Length, ["elements"] = elements, ["typedRecordCandidates"] = typedRecords, ["typedRecordCount"] = typedRecords.Count };
    }

    private static object Typed(string value)
    {
        if (bool.TryParse(value, out bool boolean)) return boolean;
        if (int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int integer)) return integer;
        if (double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out double number)) return number;
        return value;
    }

    private static int Count(Dictionary<string, List<object>> groups, string key) => groups.TryGetValue(key, out var values) ? values.Count : 0;

    private static List<Dictionary<string, object?>> TypedRecordCandidates(IEnumerable<Dictionary<string, object?>> labels)
    {
        var result = new List<Dictionary<string, object?>>();
        foreach (var label in labels.Take(4096))
        {
            if (label["value"] is not string value || value.Length == 0) continue;
            var domains = DomainHints(value);
            if (domains.Count == 0) continue;
            string lower = value.ToLowerInvariant();
            string recordType = value.StartsWith("rage__cutfObjectIdEvent9", StringComparison.Ordinal) ? "cutsceneEvent" :
                value.StartsWith("rage__cutf", StringComparison.Ordinal) ? "cutsceneType" :
                lower.Contains("time") || lower.Contains("delay") || lower.Contains("duration") || lower.Contains("timeout") || lower.Contains("phase") ? "timingField" : "resourceIdentifier";
            result.Add(new Dictionary<string, object?>
            {
                ["recordType"] = recordType,
                ["value"] = value,
                ["sourceOffset"] = label.TryGetValue("offset", out var offset) ? offset : null,
                ["domains"] = domains.ToArray(),
                ["confidence"] = "lexical-candidate",
                ["evidence"] = "printable identifier matched conservative domain tokens; binary record fields remain unresolved"
            });
        }
        return result;
    }

    private static List<string> DomainHints(string value)
    {
        string lower = value.ToLowerInvariant();
        var tokens = new Dictionary<string, string[]>(StringComparer.Ordinal)
        {
            ["camera"] = ["camera", "cam", "cutscene", "cinematic", "shot"],
            ["actor"] = ["actor", "ped", "player", "character", "npc", "animal", "john", "jack"],
            ["ai"] = ["ai", "brain", "behavior", "behaviour", "react", "attack", "patrol", "cover", "target", "capability"],
            ["prop"] = ["prop", "object", "weapon", "vehicle", "model", "attach", "locator", "bone"],
            ["audio"] = ["audio", "sound", "voice", "speech", "shout", "music", "subtitle"],
            ["animation"] = ["anim", "motion", "posture", "gesture", "walk", "run", "idle", "clip"],
            ["timing"] = ["time", "delay", "duration", "timer", "timeout", "phase", "sequence", "event"]
        };
        return tokens.Where(pair => pair.Value.Any(token => lower.Contains(token, StringComparison.Ordinal))).Select(pair => pair.Key).ToList();
    }

    private static uint? ReadUInt32(byte[] data, int offset) => offset + 4 <= data.Length ? BitConverter.ToUInt32(data, offset) : null;
    private static ulong? ReadSegmentSize(byte[] data, int offset)
    {
        uint? flags = ReadUInt32(data, offset);
        if (flags is not uint value) return null;
        ulong baseSize = 0x200UL << (int)(value & 0xF);
        ulong pages = ((value >> 27) & 1)
            + (((value >> 26) & 1) << 1)
            + (((value >> 25) & 1) << 2)
            + (((value >> 24) & 1) << 3)
            + (((value >> 17) & 0x7F) << 4)
            + (((value >> 11) & 0x3F) << 5)
            + (((value >> 7) & 0xF) << 6)
            + (((value >> 5) & 0x3) << 7)
            + (((value >> 4) & 1) << 8);
        return baseSize * pages;
    }

    private static Dictionary<string, object?> ExpandedSegments(byte[] data, string magic)
    {
        // The packed page-count bitfield is validated for the older RSC05/06
        // family.  RSC85/86 uses a different resource/page transform; applying
        // the legacy formula produced false sizes (for example 512 bytes for
        // a multi-page mission).  Virtual-page sizing is reported separately;
        // only physical-page relocation remains unresolved here.
        if (magic is "RSC\x85" or "RSC\x86")
            return new Dictionary<string, object?>
            {
                ["systemBytes"] = null,
                ["graphicsBytes"] = null,
                ["totalBytes"] = null,
                ["status"] = "validated-rsc85-extended-flags; physical-page-mapping-unresolved",
                ["formula"] = "RSC85/RSC86 virtual pages and object start are decoded from extended flags; physical page mapping is resource-specific"
            };
        ulong? system = ReadSegmentSize(data, 8);
        ulong? graphics = ReadSegmentSize(data, 12);
        return new Dictionary<string, object?>
        {
            ["systemBytes"] = system,
            ["graphicsBytes"] = graphics,
            ["totalBytes"] = system is ulong s && graphics is ulong g ? s + g : null,
            ["status"] = "legacy-rsc05-rsc06-bitfield-formula"
        };
    }

    internal static Dictionary<string, object?>? Rsc85Layout(byte[] data, string magic)
    {
        if (magic is not ("RSC\x85" or "RSC\x86") || data.Length < 16) return null;
        uint flag1 = ReadUInt32(data, 8) ?? 0;
        uint flag2 = ReadUInt32(data, 12) ?? 0;
        int[] virtualCounts = [(int)((flag1 >> 14) & 3), (int)((flag1 >> 8) & 0x3F), (int)(flag1 & 0xFF)];
        int[] physicalCounts = [(int)((flag1 >> 28) & 7), (int)((flag1 >> 24) & 0xF), (int)((flag1 >> 16) & 0xFF)];
        int objectStartPage = (int)((flag2 >> 28) & 7);
        int objectStartPageSize = 4096 << objectStartPage;
        int totalVirtual = (int)((flag2 & 0x3FFF) << 12);
        int totalPhysical = (int)(((flag2 >> 14) & 0x3FFF) << 12);
        var pages = new List<int>();
        int remaining = totalVirtual;
        int pageSize = 524288;
        foreach (int count in virtualCounts.Append(int.MaxValue))
        {
            for (int index = 0; index < count && remaining > 0; index++)
            {
                while (pageSize > remaining) pageSize >>= 1;
                pages.Add(pageSize);
                remaining -= pageSize;
            }
            pageSize >>= 1;
            if (remaining <= 0) break;
        }
        int objectStart = 0;
        bool found = false;
        foreach (int size in pages)
        {
            if (size == objectStartPageSize) { found = true; break; }
            objectStart += size;
        }
        if (!found) objectStart = 0;
        return new Dictionary<string, object?>
        {
            ["flag1"] = $"0x{flag1:X8}",
            ["flag2"] = $"0x{flag2:X8}",
            ["virtualPageCounts"] = virtualCounts,
            ["physicalPageCounts"] = physicalCounts,
            ["objectStartPage"] = objectStartPage,
            ["objectStartPageSize"] = objectStartPageSize,
            ["totalVirtualBytes"] = totalVirtual,
            ["totalPhysicalBytes"] = totalPhysical,
            ["virtualPageSizes"] = pages,
            ["objectStart"] = objectStart,
            ["status"] = "validated-rsc85-virtual-page-layout",
        };
    }
    private static string Bucket(string value)
    {
        string lower = value.ToLowerInvariant();
        if (value.StartsWith("rage__cutf", StringComparison.Ordinal)) return "type";
        if (lower.Contains("camera") || lower.Contains("cut")) return "camera";
        if (lower.Contains("audio") || lower.Contains("sound")) return "audio";
        if (lower.Contains("particle") || lower.Contains("effect")) return "effect";
        if (lower.Contains("subtitle") || lower.Contains("language")) return "subtitle";
        if (lower.Contains("anim") || lower.Contains("ped") || lower.Contains("character")) return "actor";
        if (lower.Contains("prop") || lower.Contains("weapon") || lower.Contains("model")) return "prop";
        return lower.StartsWith("f") || lower.StartsWith("v") || lower.StartsWith("i") || lower.StartsWith("b") || lower.StartsWith("c") || lower.StartsWith("p") ? "property" : "string";
    }

    private static List<Dictionary<string, object?>> ExtractStrings(byte[] data)
    {
        var result = new List<Dictionary<string, object?>>();
        for (int start = 0; start < data.Length;)
        {
            while (start < data.Length && (data[start] < 0x20 || data[start] > 0x7E)) start++;
            int end = start; while (end < data.Length && data[end] >= 0x20 && data[end] <= 0x7E) end++;
            if (end - start >= 4) result.Add(new() { ["offset"] = start, ["value"] = Encoding.ASCII.GetString(data, start, end - start) });
            start = Math.Max(end + 1, start + 1);
        }
        return result;
    }
}
