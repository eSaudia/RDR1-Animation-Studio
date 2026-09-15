using System.Security.Cryptography;

namespace RDR1AnimationStudio.Core;

/// <summary>
/// RSC85 v2 payload transform used by the PC/Switch resource reader.
/// Magic-RDR applies AES-256-ECB decryption sixteen times to each complete
/// block; a short trailing block is deliberately preserved.
/// </summary>
internal static class Rsc85Crypto
{
    private static readonly byte[] EncryptionKey = Convert.FromHexString(
        "B762DFB6E2B2C6DEAF722A32D2FB6F0C" +
        "98A3217462C9C4EDADAA2ED0DDF92F10");

    public static bool IsEncryptedResource(byte[] resource)
    {
        return resource.Length >= 8 &&
            resource[0] == (byte)'R' && resource[1] == (byte)'S' && resource[2] == (byte)'C' &&
            (resource[3] == 0x85 || resource[3] == 0x86) &&
            BitConverter.ToUInt32(resource, 4) == 2;
    }

    public static byte[] DecryptPayload(byte[] payload)
    {
        byte[] output = payload.ToArray();
        int completeLength = output.Length & ~15;
        if (completeLength == 0) return output;
        using Aes aes = Aes.Create();
        aes.KeySize = 256;
        aes.BlockSize = 128;
        aes.Mode = CipherMode.ECB;
        aes.Padding = PaddingMode.None;
        aes.Key = EncryptionKey;
        using ICryptoTransform decryptor = aes.CreateDecryptor();
        for (int pass = 0; pass < 16; pass++)
            decryptor.TransformBlock(output, 0, completeLength, output, 0);
        return output;
    }

    public static Dictionary<string, object?> Describe(byte[] payload)
    {
        byte[] transformed = DecryptPayload(payload);
        bool zstandard = transformed.Length >= 4 &&
            transformed[0] == 0x28 && transformed[1] == 0xB5 &&
            transformed[2] == 0x2F && transformed[3] == 0xFD;
        return new Dictionary<string, object?>
        {
            ["format"] = "AES-256-ECB",
            ["passes"] = 16,
            ["keyId"] = "Magic-RDR-AppGlobals.EncryptionKey",
            ["inputSize"] = payload.Length,
            ["outputSize"] = transformed.Length,
            ["transformedPayloadSha256"] = Convert.ToHexString(SHA256.HashData(transformed)),
            ["transformedPayloadPrefixHex"] = Convert.ToHexString(transformed[..Math.Min(transformed.Length, 64)]),
            ["compressionHint"] = zstandard ? "Zstandard frame" : "unknown",
        };
    }
}
