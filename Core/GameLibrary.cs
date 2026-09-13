using System.Collections.Concurrent;

namespace RDR1AnimationStudio.Core;

internal enum GameAssetKind
{
    AnimationSet,
    Character
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
        var animationSets = new ConcurrentBag<GameAsset>();
        var characters = new ConcurrentBag<GameAsset>();
        var errors = new ConcurrentBag<string>();
        int completed = 0;

        Parallel.ForEach(archivePaths, new ParallelOptions {
            CancellationToken = cancellationToken,
            MaxDegreeOfParallelism = Math.Max(1, Math.Min(4, Environment.ProcessorCount))
        }, archivePath =>
        {
            try
            {
                using Rpf6Archive archive = Rpf6Archive.Open(archivePath, names);
                foreach (Rpf6Archive.Entry entry in archive.Entries)
                {
                    if (entry.IsDirectory) continue;
                    GameAssetKind? kind = Classify(entry.Name);
                    if (kind is null && entry.IsResource && entry.ResourceType == 6)
                        kind = GameAssetKind.AnimationSet;
                    if (kind is null) continue;
                    var asset = new GameAsset(kind.Value, entry.Name, archivePath, entry.Path, entry.Index, entry.Size);
                    if (kind == GameAssetKind.AnimationSet) animationSets.Add(asset);
                    else characters.Add(asset);
                }
            }
            catch (Exception ex)
            {
                errors.Add($"{archivePath}: {ex.Message}");
            }
            int count = Interlocked.Increment(ref completed);
            progress?.Report($"Indexing game archives… {count:N0}/{archivePaths.Length:N0}");
        });

        return new GameLibrary {
            GameDirectory = root,
            Archives = archivePaths,
            AnimationSets = animationSets.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            Characters = characters.OrderBy(a => a.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
            Errors = errors.Order().ToArray()
        };
    }

    public static byte[] Extract(GameAsset asset, IReadOnlyDictionary<uint, string> names)
    {
        using Rpf6Archive archive = Rpf6Archive.Open(asset.ArchivePath, names);
        if (asset.EntryIndex < 0 || asset.EntryIndex >= archive.Entries.Count)
            throw new InvalidDataException("The indexed game asset is no longer present in its archive.");
        return archive.Extract(archive.Entries[asset.EntryIndex]);
    }

    private static GameAssetKind? Classify(string name)
    {
        string extension = Path.GetExtension(name).ToLowerInvariant();
        return extension switch {
            ".was" => GameAssetKind.AnimationSet,
            // WFT is the skinned fragment model. WPDT/WFD companions are not independently previewable.
            ".wft" => GameAssetKind.Character,
            _ => null
        };
    }
}
