using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace RDR1AnimationStudio.Core;

internal enum GameAssetKind
{
    AnimationSet,
    Character,
    MissionScript,
    Camera,
    Prop,
    AiData,
    Audio
}

internal sealed record GameAsset(
    GameAssetKind Kind,
    string Name,
    string ArchivePath,
    string InternalPath,
    int EntryIndex,
    int Size);

internal sealed class GameLibrary
{
    public required string GameDirectory { get; init; }
    public required IReadOnlyList<string> Archives { get; init; }
    public required IReadOnlyList<GameAsset> AnimationSets { get; init; }
    public required IReadOnlyList<GameAsset> Characters { get; init; }
    public required IReadOnlyList<GameAsset> MissionScripts { get; init; }
    public required IReadOnlyList<GameAsset> Cameras { get; init; }
    public required IReadOnlyList<GameAsset> Props { get; init; }
    public required IReadOnlyList<GameAsset> AiData { get; init; }
    public required IReadOnlyList<GameAsset> Audio { get; init; }
    public required IReadOnlyList<string> Errors { get; init; }

    public static Task<GameLibrary> ScanAsync(
        string gameDirectory,
        IReadOnlyDictionary<uint, string> names,
        IProgress<string>? progress = null,
        CancellationToken cancellationToken = default)
    {
        return Task.Run(() => Scan(gameDirectory, names, progress, cancellationToken), cancellationToken);
    }

    private static GameLibrary Scan(
        string gameDirectory,
        IReadOnlyDictionary<uint, string> names,
        IProgress<string>? progress,
        CancellationToken cancellationToken)
    {
        string root = Path.GetFullPath(gameDirectory);
        string[] archivePaths = Directory.EnumerateFiles(root, "*.rpf", SearchOption.AllDirectories)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        var cachedArchives = LoadArchiveCache(root, names.Count);
        var animationSets = new ConcurrentBag<GameAsset>();
        var characters = new ConcurrentBag<GameAsset>();
        var missionScripts = new ConcurrentBag<GameAsset>();
        var cameras = new ConcurrentBag<GameAsset>();
        var props = new ConcurrentBag<GameAsset>();
        var aiData = new ConcurrentBag<GameAsset>();
        var audio = new ConcurrentBag<GameAsset>();
        var errors = new ConcurrentBag<string>();
        var archiveResults = new ConcurrentDictionary<string, ArchiveCache>(StringComparer.OrdinalIgnoreCase);
        int completed = 0;
        int cached = 0;

        Parallel.ForEach(archivePaths, new ParallelOptions {
            CancellationToken = cancellationToken,
            MaxDegreeOfParallelism = Math.Max(1, Math.Min(4, Environment.ProcessorCount))
        }, archivePath =>
        {
            try
            {
                FileInfo info = new(archivePath);
                if (cachedArchives.TryGetValue(archivePath, out ArchiveCache? saved) &&
                    saved.Length == info.Length && saved.LastWriteUtcTicks == info.LastWriteTimeUtc.Ticks)
                {
                    foreach (GameAsset asset in saved.Assets)
                    {
                        AddAsset(asset, animationSets, characters, missionScripts, cameras, props, aiData, audio);
                    }
                    foreach (string error in saved.Errors) errors.Add(error);
                    archiveResults[archivePath] = saved;
                    Interlocked.Increment(ref cached);
                }
                else
                {
                using Rpf6Archive archive = Rpf6Archive.Open(archivePath, names);
                var found = new List<GameAsset>();
                    foreach (Rpf6Archive.Entry entry in archive.Entries)
                {
                    if (entry.IsDirectory) continue;
                    GameAssetKind? kind = Classify(entry.Name, entry.Path);
                    if (kind is null && entry.IsResource && entry.ResourceType == 6)
                        kind = GameAssetKind.AnimationSet;
                    if (kind is null) continue;
                    var asset = new GameAsset(kind.Value, entry.Name, archivePath, entry.Path, entry.Index, entry.Size);
                    found.Add(asset);
                    AddAsset(asset, animationSets, characters, missionScripts, cameras, props, aiData, audio);
                }
                archiveResults[archivePath] = new ArchiveCache(archivePath, info.Length, info.LastWriteTimeUtc.Ticks, found, []);
                }
            }
            catch (Exception ex)
            {
                errors.Add($"{archivePath}: {ex.Message}");
                try
                {
                    FileInfo info = new(archivePath);
                    archiveResults[archivePath] = new ArchiveCache(archivePath, info.Length, info.LastWriteTimeUtc.Ticks, [], [$"{archivePath}: {ex.Message}"]);
                }
                catch { }
            }
            int count = Interlocked.Increment(ref completed);
            progress?.Report($"Scanning game archives… {count:N0}/{archivePaths.Length:N0} ({cached:N0} cached)");
        });

        SaveArchiveCache(root, names.Count, archiveResults.Values);

        return new GameLibrary {
            GameDirectory = root,
            Archives = archivePaths,
            AnimationSets = animationSets.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            Characters = characters.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            MissionScripts = missionScripts.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            Cameras = cameras.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            Props = props.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            AiData = aiData.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            Audio = audio.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            Errors = errors.Order().ToArray()
        };
    }

    private sealed record ArchiveCache(
        string Path,
        long Length,
        long LastWriteUtcTicks,
        List<GameAsset> Assets,
        List<string> Errors);

    private sealed record ScanCache(
        int Version,
        string Root,
        int NameCount,
        List<ArchiveCache> Archives);

    private static void AddAsset(
        GameAsset asset,
        ConcurrentBag<GameAsset> animationSets,
        ConcurrentBag<GameAsset> characters,
        ConcurrentBag<GameAsset> missionScripts,
        ConcurrentBag<GameAsset> cameras,
        ConcurrentBag<GameAsset> props,
        ConcurrentBag<GameAsset> aiData,
        ConcurrentBag<GameAsset> audio)
    {
        switch (asset.Kind)
        {
            case GameAssetKind.AnimationSet: animationSets.Add(asset); break;
            case GameAssetKind.Character: characters.Add(asset); break;
            case GameAssetKind.MissionScript: missionScripts.Add(asset); break;
            case GameAssetKind.Camera: cameras.Add(asset); break;
            case GameAssetKind.Prop: props.Add(asset); break;
            case GameAssetKind.AiData: aiData.Add(asset); break;
            case GameAssetKind.Audio: audio.Add(asset); break;
        }
    }

    private static Dictionary<string, ArchiveCache> LoadArchiveCache(string root, int nameCount)
    {
        try
        {
            string path = CachePath(root);
            if (!File.Exists(path)) return new(StringComparer.OrdinalIgnoreCase);
            ScanCache? cache = JsonSerializer.Deserialize<ScanCache>(File.ReadAllText(path));
            if (cache is null || cache.Version != 2 || !string.Equals(cache.Root, root, StringComparison.OrdinalIgnoreCase) || cache.NameCount != nameCount)
                return new(StringComparer.OrdinalIgnoreCase);
            return cache.Archives.ToDictionary(item => item.Path, StringComparer.OrdinalIgnoreCase);
        }
        catch { return new(StringComparer.OrdinalIgnoreCase); }
    }

    private static void SaveArchiveCache(string root, int nameCount, IEnumerable<ArchiveCache> archives)
    {
        try
        {
            string path = CachePath(root);
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path)!);
            string temp = path + ".tmp";
            File.WriteAllText(temp, JsonSerializer.Serialize(new ScanCache(2, root, nameCount, archives.OrderBy(a => a.Path, StringComparer.OrdinalIgnoreCase).ToList())));
            File.Move(temp, path, true);
        }
        catch { /* A cache must never make game scanning fail. */ }
    }

    private static string CachePath(string root)
    {
        string key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(root)))[..24];
        return System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "Cache", "archives-" + key + ".json");
    }

    public static byte[] Extract(GameAsset asset, IReadOnlyDictionary<uint, string> names)
    {
        using Rpf6Archive archive = Rpf6Archive.Open(asset.ArchivePath, names);
        if (asset.EntryIndex < 0 || asset.EntryIndex >= archive.Entries.Count)
            throw new InvalidDataException("The indexed game asset is no longer present in its archive.");
        return archive.Extract(archive.Entries[asset.EntryIndex]);
    }

    private static GameAssetKind? Classify(string name, string path)
    {
        string extension = Path.GetExtension(name).ToLowerInvariant();
        GameAssetKind? byExtension = extension switch {
            ".was" => GameAssetKind.AnimationSet,
            // WFT is the skinned fragment model. WFD/WTD are native drawable and
            // texture companions; keep them visible so the studio can extract
            // and inspect them without requiring CodeX.
            ".wft" or ".wfd" => GameAssetKind.Character,
            ".wtd" => GameAssetKind.Character,
            ".xsc" or ".csc" or ".ysc" or ".scr" => GameAssetKind.MissionScript,
            ".awc" or ".rel" or ".xma" or ".xwb" or ".wav" => GameAssetKind.Audio,
            ".wdr" or ".wbn" or ".wbd" or ".wvd" or ".wpl" or ".ymap" or ".ydr" or ".ybn" => GameAssetKind.Prop,
            ".nav" or ".ait" or ".ai" => GameAssetKind.AiData,
            ".cam" or ".cut" or ".cin" => GameAssetKind.Camera,
            _ => null
        };
        if (byExtension is not null) return byExtension;
        string haystack = (name + "/" + path).ToLowerInvariant();
        if (haystack.Contains("camera") || haystack.Contains("cutscene") || haystack.Contains("cinematic")) return GameAssetKind.Camera;
        if (haystack.Contains("audio") || haystack.Contains("sound") || haystack.Contains("voice") || haystack.Contains("music")) return GameAssetKind.Audio;
        if (haystack.Contains("scenario") || haystack.Contains("behavior") || haystack.Contains("behaviour") || haystack.Contains("task") || haystack.Contains("navigation")) return GameAssetKind.AiData;
        if (haystack.Contains("mission") || haystack.Contains("script") || haystack.Contains("sequence")) return GameAssetKind.MissionScript;
        return null;
    }
}
