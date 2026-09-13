namespace RDR1AnimationStudio;

using RDR1AnimationStudio.Core;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        if (args.Length == 3 && args[0] == "--collect-game")
        {
            string bundled = Path.Combine(AppContext.BaseDirectory, "Assets", "ImportedFileNames.txt");
            var names = Rpf6Archive.LoadNames(bundled);
            var library = GameLibrary.ScanAsync(args[1], names).GetAwaiter().GetResult();
            Directory.CreateDirectory(args[2]);
            var sources = new List<MainForm.SourceFile>();
            foreach (var group in library.AnimationSets.GroupBy(a => a.ArchivePath))
            {
                using var archive = Rpf6Archive.Open(group.Key, names);
                foreach (var asset in group)
                {
                    byte[] bytes = archive.Extract(archive.Entries[asset.EntryIndex]);
                    string path = Path.GetFullPath(Path.Combine(args[2], Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(bytes)) + ".was"));
                    if (!File.Exists(path)) File.WriteAllBytes(path, bytes);
                    sources.Add(new(path, Path.GetRelativePath(args[1], group.Key) + "/" + asset.InternalPath));
                }
            }
            File.WriteAllText(Path.Combine(args[2], "sources.json"), System.Text.Json.JsonSerializer.Serialize(sources));
            Console.WriteLine($"collected={sources.Count} archive_errors={library.Errors.Count}");
            return;
        }
        if (args.Length >= 2 && args[0] == "--snapshot")
        {
            ApplicationConfiguration.Initialize();
            using var form = new MainForm();
            form.SaveSnapshot(args[1], args.Length > 2 ? args[2] : null);
            return;
        }
        if (args.Length == 1 && args[0] == "--probe-python")
        {
            Console.WriteLine(MainForm.FindWorkingPython() ?? "NOT_FOUND");
            return;
        }
        if (args.Length >= 2 && args[0] == "--probe-rpf")
        {
            Dictionary<uint, string>? names = args.Length >= 3 ? Rpf6Archive.LoadNames(args[2]) : null;
            using Rpf6Archive archive = Rpf6Archive.Open(args[1], names);
            Rpf6Archive.Entry[] animationSets = archive.Entries
                .Where(e => !e.IsDirectory && e.Name.EndsWith(".was", StringComparison.OrdinalIgnoreCase)).ToArray();
            Console.WriteLine($"entries={archive.Entries.Count} directories={archive.Entries.Count(e => e.IsDirectory)} was={animationSets.Length}");
            foreach (Rpf6Archive.Entry entry in animationSets.Take(10))
                Console.WriteLine($"{entry.Path}|{entry.Size}|{entry.ResourceType}");
            if (args.Length >= 4 && animationSets.Length > 0)
            {
                File.WriteAllBytes(args[3], archive.Extract(animationSets[0]));
                Console.WriteLine($"extracted={args[3]}");
            }
            return;
        }
        if (args.Length >= 2 && args[0] == "--probe-game")
        {
            string bundledNames = Path.Combine(AppContext.BaseDirectory, "Assets", "ImportedFileNames.txt");
            Dictionary<uint, string> names = File.Exists(bundledNames) ? Rpf6Archive.LoadNames(bundledNames) : [];
            GameLibrary library = GameLibrary.ScanAsync(args[1], names).GetAwaiter().GetResult();
            Console.WriteLine($"archives={library.Archives.Count} characters={library.Characters.Count} animations={library.AnimationSets.Count} errors={library.Errors.Count}");
            foreach (GameAsset character in library.Characters
                .Where(asset => asset.Name.Contains("john", StringComparison.OrdinalIgnoreCase) ||
                                asset.Name.Contains("jack", StringComparison.OrdinalIgnoreCase))
                .Take(20))
                Console.WriteLine($"character={character.Name}|{Path.GetFileName(character.ArchivePath)}|{character.InternalPath}");
            if (args.Length >= 4)
            {
                GameAsset? match = library.Characters.FirstOrDefault(asset =>
                    asset.Name.Contains(args[2], StringComparison.OrdinalIgnoreCase));
                if (match is null) throw new InvalidOperationException($"No character matched '{args[2]}'.");
                File.WriteAllBytes(args[3], GameLibrary.Extract(match, names));
                Console.WriteLine($"extracted={match.Name}|{args[3]}");
            }
            return;
        }
        ApplicationConfiguration.Initialize();
        Application.Run(new MainForm());
    }
}
