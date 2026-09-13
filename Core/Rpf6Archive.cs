using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

namespace RDR1AnimationStudio.Core;

internal sealed class Rpf6Archive : IDisposable
{
    internal sealed class Entry
    {
        public required int Index { get; init; }
        public required uint NameHash { get; init; }
        public required string Name { get; set; }
        public required bool IsDirectory { get; init; }
        public int FirstChild { get; init; }
        public int ChildCount { get; init; }
        public int Size { get; init; }
        public int RawOffset { get; init; }
        public int Flag1 { get; init; }
        public int Flag2 { get; init; }
        public Entry? Parent { get; set; }
        public List<Entry> Children { get; } = [];
        public bool IsResource => !IsDirectory && (Flag1 & unchecked((int)0x80000000)) != 0;
        public byte ResourceType => (byte)(RawOffset & 0xff);
        public long DataOffset => IsResource ? (RawOffset & 0x7fffff00L) * 8L : (RawOffset & 0x7fffffffL) * 8L;
        public string Path => Parent is null ? Name : Parent.Path + "/" + Name;
        public override string ToString() => Name;
    }

    private readonly FileStream stream;
    public string FilePath { get; }
    public IReadOnlyList<Entry> Entries { get; }
    public Entry Root => Entries[0];
    public bool IsEncrypted { get; }

    private Rpf6Archive(string path, FileStream source, IReadOnlyList<Entry> entries, bool encrypted)
    {
        FilePath = path;
        stream = source;
        Entries = entries;
        IsEncrypted = encrypted;
    }

    public static Rpf6Archive Open(string path, IReadOnlyDictionary<uint, string>? names = null)
    {
        var source = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        try
        {
            Span<byte> header = stackalloc byte[16];
            ReadExactly(source, header);
            if (BinaryPrimitives.ReadUInt32BigEndian(header) != 0x52504636)
                throw new InvalidDataException("This is not an RPF6 archive.");
            int count = BinaryPrimitives.ReadInt32BigEndian(header[4..]);
            int encryption = BinaryPrimitives.ReadInt32BigEndian(header[12..]);
            if (count <= 0 || count > 2_000_000) throw new InvalidDataException("Invalid RPF entry count.");
            int tocSize = checked((count * 20 + 15) & ~15);
            byte[] toc = new byte[tocSize];
            ReadExactly(source, toc);
            if (encryption != 0) DecryptToc(toc);
            var entries = new List<Entry>(count);
            for (int i = 0; i < count; i++)
            {
                ReadOnlySpan<byte> row = toc.AsSpan(i * 20, 20);
                uint hash = BinaryPrimitives.ReadUInt32BigEndian(row);
                int second = BinaryPrimitives.ReadInt32BigEndian(row[4..]);
                int third = BinaryPrimitives.ReadInt32BigEndian(row[8..]);
                bool directory = (row[8] & 0x80) != 0;
                string name = i == 0 && hash == 0 ? "root" : names?.GetValueOrDefault(hash) ?? $"0x{hash:X8}";
                entries.Add(directory
                    ? new Entry { Index = i, NameHash = hash, Name = name, IsDirectory = true,
                        FirstChild = third & int.MaxValue,
                        ChildCount = BinaryPrimitives.ReadInt32BigEndian(row[12..]) & 0x0fffffff }
                    : new Entry { Index = i, NameHash = hash, Name = name, IsDirectory = false,
                        Size = second & 0x0fffffff, RawOffset = third,
                        Flag1 = BinaryPrimitives.ReadInt32BigEndian(row[12..]),
                        Flag2 = BinaryPrimitives.ReadInt32BigEndian(row[16..]) });
            }
            foreach (Entry directory in entries.Where(e => e.IsDirectory))
            {
                int end = checked(directory.FirstChild + directory.ChildCount);
                if (directory.FirstChild < 0 || end > entries.Count) throw new InvalidDataException("Invalid RPF directory range.");
                for (int i = directory.FirstChild; i < end; i++)
                {
                    entries[i].Parent = directory;
                    directory.Children.Add(entries[i]);
                }
            }
            return new Rpf6Archive(System.IO.Path.GetFullPath(path), source, entries, encryption != 0);
        }
        catch { source.Dispose(); throw; }
    }

    public byte[] Extract(Entry entry)
    {
        if (entry.IsDirectory) throw new InvalidOperationException("Directories cannot be extracted.");
        if (entry.DataOffset < 0 || entry.DataOffset + entry.Size > stream.Length)
            throw new InvalidDataException("The file entry points outside the archive.");
        byte[] data = new byte[entry.Size];
        lock (stream)
        {
            stream.Position = entry.DataOffset;
            ReadExactly(stream, data);
        }
        return data;
    }

    public void Dispose() => stream.Dispose();

    private static void ReadExactly(Stream input, Span<byte> buffer)
    {
        int read = 0;
        while (read < buffer.Length)
        {
            int count = input.Read(buffer[read..]);
            if (count == 0) throw new EndOfStreamException();
            read += count;
        }
    }

    private static void DecryptToc(byte[] data)
    {
        byte[] key =
        [
            0xB7, 0x62, 0xDF, 0xB6, 0xE2, 0xB2, 0xC6, 0xDE,
            0xAF, 0x72, 0x2A, 0x32, 0xD2, 0xFB, 0x6F, 0x0C,
            0x98, 0xA3, 0x21, 0x74, 0x62, 0xC9, 0xC4, 0xED,
            0xAD, 0xAA, 0x2E, 0xD0, 0xDD, 0xF9, 0x2F, 0x10
        ];
        int length = data.Length & ~15;
        using Aes aes = Aes.Create();
        aes.KeySize = 256;
        aes.Mode = CipherMode.ECB;
        aes.Padding = PaddingMode.None;
        aes.Key = key;
        using ICryptoTransform decryptor = aes.CreateDecryptor();
        for (int pass = 0; pass < 16; pass++)
            decryptor.TransformBlock(data, 0, length, data, 0);
    }

    public static Dictionary<uint, string> LoadNames(string path)
    {
        var result = new Dictionary<uint, string>();
        foreach (string line in File.ReadLines(path, Encoding.UTF8))
        {
            string name = line.Trim();
            if (name.Length > 0) result.TryAdd(Hash(name), name);
        }
        return result;
    }

    private static uint Hash(string value)
    {
        uint hash = 0;
        foreach (char character in value.ToLowerInvariant())
        {
            hash += character;
            hash += hash << 10;
            hash ^= hash >> 6;
        }
        hash += hash << 3;
        hash ^= hash >> 11;
        hash += hash << 15;
        return hash;
    }
}
