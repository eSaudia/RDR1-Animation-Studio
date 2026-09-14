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
        if (RscMagics.Contains(magic)) return new() { ["format"] = "RSC", ["confidence"] = "verified RSC wrapper", ["magic"] = magic, ["actualSize"] = data.Length, ["unresolved"] = new[] { "payload compression/resource graph requires the resource-specific decoder" } };
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
        return new()
        {
            ["format"] = "RBF0", ["confidence"] = "verified container and labels; scalar encodings remain raw",
            ["class"] = className, ["declaredSize"] = data.Length >= 8 ? BitConverter.ToUInt32(data, 4) : null, ["actualSize"] = data.Length,
            ["semanticCounts"] = new Dictionary<string, int> { ["objectTypes"] = objectTypes, ["eventRecords"] = events, ["cameraLabels"] = Count(groups, "camera"), ["audioLabels"] = Count(groups, "audio"), ["effectLabels"] = Count(groups, "effect"), ["subtitleLabels"] = Count(groups, "subtitle"), ["actorLabels"] = Count(groups, "actor"), ["propLabels"] = Count(groups, "prop") },
            ["labels"] = groups,
            ["unresolved"] = new[] { "numeric property payloads are preserved as bytes until the RBF value-tag table is validated", "event timing and camera transforms require decoding the typed record stream" }
        };
    }

    private static Dictionary<string, object?> DecodeXml(string text)
    {
        var root = XDocument.Parse(text).Root ?? throw new InvalidDataException("XML resource has no root element.");
        var elements = root.DescendantsAndSelf().Select(element => new Dictionary<string, object?> { ["tag"] = element.Name.LocalName, ["attributes"] = element.Attributes().ToDictionary(a => a.Name.LocalName, a => (object?)Typed(a.Value)) }).ToArray();
        return new() { ["format"] = "XML", ["confidence"] = "verified XML element and attribute values", ["root"] = root.Name.LocalName, ["elementCount"] = elements.Length, ["elements"] = elements };
    }

    private static object Typed(string value)
    {
        if (bool.TryParse(value, out bool boolean)) return boolean;
        if (int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int integer)) return integer;
        if (double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out double number)) return number;
        return value;
    }

    private static int Count(Dictionary<string, List<object>> groups, string key) => groups.TryGetValue(key, out var values) ? values.Count : 0;
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
