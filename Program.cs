namespace RDR1AnimationStudio;

using System.Diagnostics;
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
        if (args.Length >= 2 && args[0] == "--inventory-rpf")
        {
            Dictionary<uint, string>? names = args.Length >= 3 ? Rpf6Archive.LoadNames(args[2]) : null;
            using Rpf6Archive archive = Rpf6Archive.Open(args[1], names);
            var files = archive.Entries.Where(e => !e.IsDirectory).ToArray();
            Console.WriteLine($"archive={Path.GetFileName(args[1])} entries={archive.Entries.Count} files={files.Length} encrypted={archive.IsEncrypted}");
            Console.WriteLine("extensions:");
            foreach (var group in files.GroupBy(e => Path.GetExtension(e.Name).ToLowerInvariant()).OrderByDescending(g => g.Count()).ThenBy(g => g.Key))
                Console.WriteLine($"  {(string.IsNullOrEmpty(group.Key) ? "<none>" : group.Key)} count={group.Count()} bytes={group.Sum(e => (long)e.Size)}");
            Console.WriteLine("resource-types:");
            foreach (var group in files.GroupBy(e => e.ResourceType).OrderByDescending(g => g.Count()).ThenBy(g => g.Key))
                Console.WriteLine($"  0x{group.Key:X2} count={group.Count()} bytes={group.Sum(e => (long)e.Size)}");
            Console.WriteLine("samples:");
            foreach (Rpf6Archive.Entry entry in files
                .Where(e => new[] { ".xsc", ".csc", ".ysc", ".scr", ".cam", ".cut", ".cin", ".awc", ".rel", ".xma", ".wav" }
                    .Contains(Path.GetExtension(e.Name), StringComparer.OrdinalIgnoreCase))
                .Take(40))
                Console.WriteLine($"  {entry.Path}|{entry.Size}|resource=0x{entry.ResourceType:X2}|offset=0x{entry.DataOffset:X}");
            Console.WriteLine("first-files:");
            foreach (Rpf6Archive.Entry entry in files.Take(20))
                Console.WriteLine($"  index={entry.Index} {entry.Path}|{entry.Size}|resource=0x{entry.ResourceType:X2}|offset=0x{entry.DataOffset:X}");
            return;
        }
        if (args.Length >= 2 && args[0] == "--decode-resource")
        {
            byte[] resource = File.ReadAllBytes(args[1]);
            string json = ResourceSemantics.DecodeToJson(resource, Path.GetFullPath(args[1]));
            if (args.Length >= 3) { File.WriteAllText(args[2], json + Environment.NewLine); Console.WriteLine($"decoded={args[2]}"); }
            else Console.WriteLine(json);
            return;
        }
        if (args.Length >= 4 && args[0] == "--extract-rpf-entry")
        {
            Dictionary<uint, string>? names = args.Length >= 5 ? Rpf6Archive.LoadNames(args[4]) : null;
            using Rpf6Archive archive = Rpf6Archive.Open(args[1], names);
            if (!int.TryParse(args[2], out int index) || index < 0 || index >= archive.Entries.Count)
                throw new ArgumentOutOfRangeException(nameof(index));
            Rpf6Archive.Entry entry = archive.Entries[index];
            if (entry.IsDirectory) throw new InvalidOperationException("The selected entry is a directory.");
            File.WriteAllBytes(args[3], archive.Extract(entry));
            Console.WriteLine($"extracted={entry.Path}|{args[3]}|size={entry.Size}|resource=0x{entry.ResourceType:X2}");
            return;
        }
        if (args.Length >= 4 && args[0] == "--extract-rpf-name")
        {
            Dictionary<uint, string>? names = args.Length >= 5 ? Rpf6Archive.LoadNames(args[4]) : null;
            using Rpf6Archive archive = Rpf6Archive.Open(args[1], names);
            string query = args[2];
            Rpf6Archive.Entry[] matches = archive.Entries
                .Where(e => !e.IsDirectory && (e.Name.Contains(query, StringComparison.OrdinalIgnoreCase) || e.Path.Contains(query, StringComparison.OrdinalIgnoreCase)))
                .ToArray();
            if (matches.Length == 0) throw new FileNotFoundException($"No RPF entry matched '{query}'.");
            Rpf6Archive.Entry selected = matches[0];
            File.WriteAllBytes(args[3], archive.Extract(selected));
            Console.WriteLine($"extracted={selected.Path}|{args[3]}|size={selected.Size}|resource=0x{selected.ResourceType:X2}|flag1=0x{selected.Flag1:X8}|flag2=0x{selected.Flag2:X8}|offset=0x{selected.DataOffset:X}|matches={matches.Length}");
            return;
        }
        if (args.Length >= 4 && args[0] == "--decode-mission")
        {
            Dictionary<uint, string>? names = args.Length >= 5 ? Rpf6Archive.LoadNames(args[4]) : null;
            using Rpf6Archive archive = Rpf6Archive.Open(args[1], names);
            string query = args[2];
            Rpf6Archive.Entry[] matches = archive.Entries
                .Where(e => !e.IsDirectory && (e.Name.Contains(query, StringComparison.OrdinalIgnoreCase) || e.Path.Contains(query, StringComparison.OrdinalIgnoreCase)))
                .ToArray();
            if (matches.Length == 0) throw new FileNotFoundException($"No RPF entry matched '{query}'.");
            Rpf6Archive.Entry selected = matches[0];
            byte[] bytes = archive.Extract(selected);
            string outputDirectory = Path.GetFullPath(args[3]);
            string manifest = MissionPackage.Write(outputDirectory, bytes, selected.Name, Path.GetFullPath(args[1]), selected.Path);
            Console.WriteLine($"package={manifest}|source={selected.Path}|size={bytes.Length}|matches={matches.Length}");
            return;
        }
        if (args.Length >= 4 && args[0] == "--probe-xmem")
        {
            // RDR1's legacy XCompress DLL is 32-bit. Run the bundled x86
            // helper as a child process so a malformed RSC85 candidate cannot
            // terminate the x64 studio. Syntax:
            //   --probe-xmem payload.bin expanded-size stream|block [--pad16] [xcompress-directory]
            string helper = Path.Combine(AppContext.BaseDirectory, "Tools", "XMemProbe", "Rdr1XMemProbe.exe");
            if (!File.Exists(helper)) throw new FileNotFoundException("The x86 XMem probe helper is missing from the installation.", helper);
            if (!int.TryParse(args[2], out int expandedSize) || expandedSize <= 0)
                throw new ArgumentException("expanded-size must be a positive integer.");
            bool pad16 = args.Skip(4).Any(value => value.Equals("--pad16", StringComparison.OrdinalIgnoreCase));
            string? suppliedDirectory = args.Skip(4).FirstOrDefault(value => !value.StartsWith("--", StringComparison.Ordinal));
            string? dllDirectory = suppliedDirectory is null ? null :
                (File.Exists(suppliedDirectory) ? Path.GetDirectoryName(Path.GetFullPath(suppliedDirectory)) : Path.GetFullPath(suppliedDirectory));
            if (dllDirectory is not null && !Directory.Exists(dllDirectory)) throw new DirectoryNotFoundException($"XCompress directory not found: {dllDirectory}");
            var probeArgs = new List<string> { args[1], expandedSize.ToString(System.Globalization.CultureInfo.InvariantCulture), args[3] };
            if (pad16) probeArgs.Add("--pad16");
            var start = new ProcessStartInfo { FileName = helper, WorkingDirectory = dllDirectory ?? Path.GetDirectoryName(helper)!, UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
            foreach (string value in probeArgs) start.ArgumentList.Add(value);
            using Process process = Process.Start(start) ?? throw new InvalidOperationException("Could not start the x86 XMem probe helper.");
            string stdout = process.StandardOutput.ReadToEnd();
            string stderr = process.StandardError.ReadToEnd();
            process.WaitForExit();
            if (stdout.Length > 0) Console.WriteLine(stdout.TrimEnd());
            if (stderr.Length > 0) Console.Error.WriteLine(stderr.TrimEnd());
            Console.WriteLine($"probeExit={process.ExitCode}");
            Environment.ExitCode = process.ExitCode;
            return;
        }
        if (args.Length >= 2 && args[0] == "--probe-game")
        {
            string bundledNames = Path.Combine(AppContext.BaseDirectory, "Assets", "ImportedFileNames.txt");
            Dictionary<uint, string> names = File.Exists(bundledNames) ? Rpf6Archive.LoadNames(bundledNames) : [];
            GameLibrary library = GameLibrary.ScanAsync(args[1], names).GetAwaiter().GetResult();
            Console.WriteLine($"archives={library.Archives.Count} characters={library.Characters.Count} animations={library.AnimationSets.Count} scripts={library.MissionScripts.Count} cameras={library.Cameras.Count} props={library.Props.Count} ai={library.AiData.Count} audio={library.Audio.Count} errors={library.Errors.Count}");
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
