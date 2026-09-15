using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace RDR1AnimationStudio.Core;

/// <summary>
/// Bounded, non-destructive analysis of an RSC85/RSC86 payload.  It preserves
/// the original bytes and, for the validated v2 wrapper, records the exact
/// AES-transformed prefix and compression signature without expanding pages.
/// </summary>
internal static class Rsc85Probe
{
    public static Dictionary<string, object?> Analyze(byte[] resource)
    {
        bool isRsc = resource.Length >= 4 && resource[0] == (byte)'R' && resource[1] == (byte)'S' && resource[2] == (byte)'C';
        if (!isRsc)
            return new() { ["status"] = "not-an-rsc-resource", ["byteLength"] = resource.Length };

        int headerLength = resource.Length >= 5 && (resource[4] == 0x05 || resource[4] == 0x06) ? 12 : 16;
        headerLength = Math.Min(headerLength, resource.Length);
        byte[] payload = resource[headerLength..];
        string magic = Encoding.Latin1.GetString(resource, 0, Math.Min(4, resource.Length));
        uint? systemFlags = ReadUInt32(resource, 8);
        uint? graphicsFlags = ReadUInt32(resource, 12);
        bool legacyPageFormula = magic is not ("RSC\x85" or "RSC\x86");
        ulong? expectedSystem = legacyPageFormula && systemFlags is uint sf ? SegmentSize(sf) : null;
        ulong? expectedGraphics = legacyPageFormula && graphicsFlags is uint gf ? SegmentSize(gf) : null;
        var report = new Dictionary<string, object?>
        {
            ["status"] = magic is "RSC\x85" or "RSC\x86" ? "rsc85-family-payload-probe" : "other-rsc-payload-probe",
            ["magic"] = magic,
            ["headerLength"] = headerLength,
            ["payloadOffset"] = headerLength,
            ["payloadSize"] = payload.Length,
            ["versionWord"] = ReadUInt32(resource, 4),
            ["payloadSha256"] = Convert.ToHexString(SHA256.HashData(payload)),
            ["expectedExpandedSegments"] = new Dictionary<string, object?>
            {
                ["systemBytes"] = expectedSystem,
                ["graphicsBytes"] = expectedGraphics,
                ["totalBytes"] = expectedSystem is ulong s && expectedGraphics is ulong g ? s + g : null,
                ["status"] = legacyPageFormula ? "legacy-rsc05-rsc06-bitfield-formula" : "validated-rsc85-extended-flags; physical-page-mapping-unresolved",
                ["formula"] = legacyPageFormula
                    ? "baseSize=(0x200 << flags[3:0]); page weights use bits 27,26,25,24,17..11,10..7,6..5,4"
                    : "RSC85/RSC86 virtual pages and object start are decoded from extended flags; physical page mapping is resource-specific"
            },
            ["entropyBitsPerByte"] = Math.Round(Entropy(payload), 4),
            ["standardFrameSignatures"] = FindSignatures(payload, headerLength),
            ["lzxHeaderCandidates"] = FindLzxCandidates(payload, headerLength),
            ["alignedWindows"] = AlignedWindows(payload, headerLength),
            ["printableRuns"] = CountPrintableRuns(payload),
            ["interpretation"] = "High entropy and absent standard frame signatures are clues only; they do not prove encryption. Candidate page headers must be validated against a known expanded resource."
        };
        if (magic is "RSC\x85" or "RSC\x86") report["rsc85Layout"] = ResourceSemantics.Rsc85Layout(resource, magic);
        if (Rsc85Crypto.IsEncryptedResource(resource))
        {
            try
            {
                var encryption = Rsc85Crypto.Describe(payload);
                report["payloadEncryption"] = encryption;
                byte[] transformed = Rsc85Crypto.DecryptPayload(payload);
                report["decryptedStandardFrameSignatures"] = FindSignatures(transformed, headerLength: 0);
                report["decryptedPrefixHex"] = Convert.ToHexString(transformed[..Math.Min(transformed.Length, 64)]);
                report["interpretation"] = "RSC85 v2 AES transform validated; decrypted payload exposes a Zstandard frame. Resource-script object layout is reported separately and VM decoding is delegated to the semantic decoder.";
            }
            catch (Exception ex)
            {
                report["decryptionError"] = ex.Message;
            }
        }
        return report;
    }

    public static string Write(string outputDirectory, byte[] resource)
    {
        Directory.CreateDirectory(outputDirectory);
        string path = Path.Combine(outputDirectory, "rsc85.probe.json");
        File.WriteAllText(path, JsonSerializer.Serialize(Analyze(resource), new JsonSerializerOptions { WriteIndented = true }) + Environment.NewLine, Encoding.UTF8);
        return path;
    }

    private static List<Dictionary<string, object?>> FindSignatures(byte[] payload, int headerLength)
    {
        (string Name, byte[] Bytes)[] signatures =
        [
            ("zstandard", [0x28, 0xB5, 0x2F, 0xFD]),
            ("zlib-header", [0x78, 0x01]),
            ("zlib-header", [0x78, 0x9C]),
            ("zlib-header", [0x78, 0xDA]),
            ("gzip", [0x1F, 0x8B])
        ];
        var result = new List<Dictionary<string, object?>>();
        foreach ((string name, byte[] bytes) in signatures)
            for (int offset = 0; offset <= payload.Length - bytes.Length && result.Count < 256; offset++)
                if (payload.AsSpan(offset, bytes.Length).SequenceEqual(bytes))
                    result.Add(new() { ["format"] = name, ["offset"] = offset, ["absoluteOffset"] = offset + headerLength, ["bytes"] = Convert.ToHexString(bytes) });
        return result;
    }

    private static List<Dictionary<string, object?>> FindLzxCandidates(byte[] payload, int headerLength)
    {
        var result = new List<Dictionary<string, object?>>();
        // The legacy LZX wrapper used by RDR tooling starts blocks with either
        // a compact two-byte size pair or FF + big-endian WORD sizes.  Record
        // plausible candidates only; do not call a native decompressor here.
        for (int offset = 0; offset + 5 < payload.Length && result.Count < 256; offset++)
        {
            if (payload[offset] != 0xFF) continue;
            int unpacked = (payload[offset + 1] << 8) | payload[offset + 2];
            int packed = (payload[offset + 3] << 8) | payload[offset + 4];
            if (unpacked is > 0 and <= 0x10000 && packed is > 0 and <= 0x10000 && offset + 5 + packed <= payload.Length)
                result.Add(new() { ["offset"] = offset, ["uncompressedSize"] = unpacked, ["compressedSize"] = packed, ["absoluteOffset"] = offset + headerLength, ["confidence"] = "shape-only candidate" });
        }
        return result;
    }

    private static List<Dictionary<string, object?>> AlignedWindows(byte[] payload, int headerLength)
    {
        var result = new List<Dictionary<string, object?>>();
        for (int offset = 0; offset < payload.Length && result.Count < 128; offset += 0x1000)
            result.Add(new() { ["offset"] = offset, ["absoluteOffset"] = offset + headerLength, ["prefixHex"] = Convert.ToHexString(payload[offset..Math.Min(payload.Length, offset + 16)]) });
        return result;
    }

    private static int CountPrintableRuns(byte[] payload)
    {
        int runs = 0, length = 0;
        foreach (byte value in payload)
        {
            if (value is >= 0x20 and <= 0x7E) { if (length++ == 0) runs++; }
            else length = 0;
        }
        return runs;
    }

    private static uint? ReadUInt32(byte[] data, int offset) => offset + 4 <= data.Length ? BitConverter.ToUInt32(data, offset) : null;

    private static ulong SegmentSize(uint flags)
    {
        ulong baseSize = 0x200UL << (int)(flags & 0xF);
        ulong pages = ((flags >> 27) & 1)
            + (((flags >> 26) & 1) << 1)
            + (((flags >> 25) & 1) << 2)
            + (((flags >> 24) & 1) << 3)
            + (((flags >> 17) & 0x7F) << 4)
            + (((flags >> 11) & 0x3F) << 5)
            + (((flags >> 7) & 0xF) << 6)
            + (((flags >> 5) & 0x3) << 7)
            + (((flags >> 4) & 1) << 8);
        return baseSize * pages;
    }

    private static double Entropy(byte[] data)
    {
        if (data.Length == 0) return 0;
        Span<int> histogram = stackalloc int[256];
        foreach (byte value in data) histogram[value]++;
        double result = 0;
        foreach (int count in histogram)
            if (count > 0)
            {
                double probability = (double)count / data.Length;
                result -= probability * Math.Log2(probability);
            }
        return result;
    }
}
