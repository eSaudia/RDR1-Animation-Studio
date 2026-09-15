using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Web.Script.Serialization;

// Build with the .NET Framework x86 compiler on Windows:
//   csc.exe /nologo /platform:x86 /target:exe /out:Rdr1XMemProbe.exe LegacyProbe.cs /r:System.Web.Extensions.dll
// Keep this helper separate from the x64 studio: xcompress32.dll cannot be
// loaded into a 64-bit process. Each invocation tests one candidate so a
// native crash never takes down the studio or hides which candidate failed.
internal static class LegacyProbe
{
    private enum CodecType { Default = 0, Lzx = 1 }
    [StructLayout(LayoutKind.Explicit)]
    private struct LzxParameters
    {
        [FieldOffset(0)] public uint Flags;
        [FieldOffset(4)] public uint WindowSize;
        [FieldOffset(8)] public uint CompressionPartitionSize;
    }

    [DllImport("xcompress32.dll", CallingConvention = CallingConvention.Winapi)]
    private static extern int XMemCreateDecompressionContext(CodecType codecType, IntPtr codecParams, uint flags, out IntPtr context);
    [DllImport("xcompress32.dll", CallingConvention = CallingConvention.Winapi)]
    private static extern int XMemResetDecompressionContext(IntPtr context);
    [DllImport("xcompress32.dll", CallingConvention = CallingConvention.Winapi)]
    private static extern int XMemDecompress(IntPtr context, byte[] destination, ref int destinationSize, byte[] source, int sourceSize);
    [DllImport("xcompress32.dll", CallingConvention = CallingConvention.Winapi)]
    private static extern int XMemDecompressStream(IntPtr context, byte[] destination, ref int destinationSize, byte[] source, ref int sourceSize);
    [DllImport("xcompress32.dll", CallingConvention = CallingConvention.Winapi)]
    private static extern void XMemDestroyDecompressionContext(IntPtr context);
    [DllImport("xcompress32.dll", CallingConvention = CallingConvention.Winapi)]
    private static extern int XMemCreateCompressionContext(CodecType codecType, ref LzxParameters parameters, uint flags, out IntPtr context);
    [DllImport("xcompress32.dll", CallingConvention = CallingConvention.Winapi)]
    private static extern void XMemDestroyCompressionContext(IntPtr context);
    [DllImport("xcompress32.dll", CallingConvention = CallingConvention.Winapi)]
    private static extern int XMemResetCompressionContext(IntPtr context);
    [DllImport("xcompress32.dll", CallingConvention = CallingConvention.Winapi)]
    private static extern int XMemCompress(IntPtr context, byte[] destination, ref int destinationSize, byte[] source, int sourceSize);

    private static int Main(string[] args)
    {
        if (args.Length == 1 && String.Equals(args[0], "--self-test", StringComparison.OrdinalIgnoreCase)) return SelfTest();
        if (args.Length < 3) { Console.Error.WriteLine("usage: LegacyProbe <payload.bin> <expanded-size> <stream|block> [--pad16]"); return 2; }
        string input = Path.GetFullPath(args[0]);
        int expanded;
        if (!Int32.TryParse(args[1], out expanded) || expanded < 1 || expanded > 268435456) return 2;
        bool stream = String.Equals(args[2], "stream", StringComparison.OrdinalIgnoreCase);
        bool pad16 = args.Skip(3).Any(a => String.Equals(a, "--pad16", StringComparison.OrdinalIgnoreCase));
        byte[] original = File.ReadAllBytes(input);
        byte[] source = pad16 ? original.Concat(new byte[16]).ToArray() : original;
        byte[] destination = new byte[expanded];
        IntPtr context;
        int create = XMemCreateDecompressionContext(CodecType.Lzx, IntPtr.Zero, 0, out context);
        var report = new Dictionary<string, object>();
        report["input"] = input; report["inputBytes"] = original.Length; report["sourceBytes"] = source.Length;
        report["expandedCapacity"] = expanded; report["mode"] = stream ? "stream" : "block"; report["pad16"] = pad16;
        report["codec"] = "XMEMCODEC_LZX"; report["createResult"] = create; report["context"] = context.ToInt64();
        if (create != 0 || context == IntPtr.Zero) { Console.WriteLine(new JavaScriptSerializer().Serialize(report)); return 3; }
        try
        {
            report["resetResult"] = XMemResetDecompressionContext(context);
            int destinationSize = destination.Length; int sourceSize = source.Length;
            int result = stream
                ? XMemDecompressStream(context, destination, ref destinationSize, source, ref sourceSize)
                : XMemDecompress(context, destination, ref destinationSize, source, source.Length);
            report["decompressResult"] = result; report["writtenBytes"] = destinationSize; report["consumedBytes"] = sourceSize;
            if (result == 0 && destinationSize > 0 && destinationSize <= destination.Length)
            {
                byte[] output = destination.Take(destinationSize).ToArray();
                string outputPath = input + "." + (stream ? "stream" : "block") + (pad16 ? ".pad16" : "") + ".expanded.bin";
                File.WriteAllBytes(outputPath, output);
                report["output"] = outputPath; report["outputSha256"] = BitConverter.ToString(SHA256.Create().ComputeHash(output)).Replace("-", "");
                report["outputPrefixHex"] = BitConverter.ToString(output.Take(32).ToArray()).Replace("-", "");
            }
        }
        finally { XMemDestroyDecompressionContext(context); }
        Console.WriteLine(new JavaScriptSerializer().Serialize(report)); return 0;
    }

    private static int SelfTest()
    {
        byte[] original = Enumerable.Repeat((byte)0x41, 4096).Concat(Enumerable.Range(0, 256).Select(i => (byte)i)).ToArray();
        var parameters = new LzxParameters { Flags = 0, WindowSize = 131072, CompressionPartitionSize = 0 };
        IntPtr compression;
        int createCompression = XMemCreateCompressionContext(CodecType.Lzx, ref parameters, 0, out compression);
        var report = new Dictionary<string, object>(); report["createCompressionResult"] = createCompression; report["context"] = compression.ToInt64();
        if (createCompression != 0 || compression == IntPtr.Zero) { Console.WriteLine(new JavaScriptSerializer().Serialize(report)); return 3; }
        try
        {
            report["resetCompressionResult"] = XMemResetCompressionContext(compression);
            byte[] packed = new byte[original.Length * 2]; int packedSize = packed.Length;
            int compressResult = XMemCompress(compression, packed, ref packedSize, original, original.Length);
            report["compressResult"] = compressResult; report["compressedBytes"] = packedSize;
            if (compressResult != 0 || packedSize <= 0) { Console.WriteLine(new JavaScriptSerializer().Serialize(report)); return 4; }
            XMemDestroyCompressionContext(compression);
            compression = IntPtr.Zero;
            IntPtr decompression;
            int createDecompression = XMemCreateDecompressionContext(CodecType.Lzx, IntPtr.Zero, 0, out decompression);
            report["createDecompressionResult"] = createDecompression;
            if (createDecompression != 0 || decompression == IntPtr.Zero) { Console.WriteLine(new JavaScriptSerializer().Serialize(report)); return 5; }
            try
            {
                XMemResetDecompressionContext(decompression);
                byte[] unpacked = new byte[original.Length]; int unpackedSize = unpacked.Length;
                int decompressResult = XMemDecompress(decompression, unpacked, ref unpackedSize, packed, packedSize);
                report["decompressResult"] = decompressResult; report["decompressedBytes"] = unpackedSize;
                report["roundTripMatch"] = decompressResult == 0 && unpackedSize == original.Length && original.SequenceEqual(unpacked.Take(unpackedSize));
            }
            finally { XMemDestroyDecompressionContext(decompression); }
        }
        finally
        {
            // The compression context is destroyed above on the normal path.
            // The native API tolerates a null handle on the early-failure path.
            if (compression != IntPtr.Zero) { try { XMemDestroyCompressionContext(compression); } catch { } }
        }
        Console.WriteLine(new JavaScriptSerializer().Serialize(report)); return 0;
    }
}
