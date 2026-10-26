using System.Security.Cryptography;
using System.Text.Json;

namespace RDR1AnimationStudio.Core;

/// <summary>
/// Writes a small, reproducible evidence package for one mission's likely
/// streamed world cell. The package records the exact bytes and the reason
/// each asset was selected; the UI runs the bounded WBD/WVD/WSI inspectors over
/// those inputs after extraction.
/// </summary>
internal static class WorldCellPackage
{
    private sealed record EvidenceFile(
        string Role,
        string Name,
        string RelativePath,
        string SourceArchive,
        string InternalPath,
        int Size,
        string Sha256,
        string Status,
        string Notes);

    public static string Write(
        GameLibrary library,
        IReadOnlyDictionary<uint, string> names,
        string missionQuery,
        string outputDirectory,
        CancellationToken cancellationToken = default)
    {
        string root = Path.GetFullPath(outputDirectory);
        Directory.CreateDirectory(root);
        var files = new List<EvidenceFile>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        GameAsset? mission = library.MissionScripts
            .Where(asset => asset.Name.Contains(missionQuery, StringComparison.OrdinalIgnoreCase))
            .OrderBy(asset => Path.GetExtension(asset.Name).Equals(".wsc", StringComparison.OrdinalIgnoreCase) ? 0 : 1)
            .ThenBy(asset => asset.Name, StringComparer.OrdinalIgnoreCase)
            .FirstOrDefault();
        if (mission is null)
            throw new FileNotFoundException($"No mission resource matched '{missionQuery}'.");

        AddAsset(files, seen, root, names, mission, "mission-script", "Verified mission resource extracted from the scanned RPF.", cancellationToken);

        // RDR stores companion mission resources beside the selected script.
        // Include the .sco companion when present, but never imply that it is
        // a decoded script: it remains a byte-preserved evidence input.
        foreach (GameAsset companion in library.MissionScripts
            .Where(asset => asset.ArchivePath.Equals(mission.ArchivePath, StringComparison.OrdinalIgnoreCase)
                && Path.GetDirectoryName(asset.InternalPath)?.Equals(Path.GetDirectoryName(mission.InternalPath), StringComparison.OrdinalIgnoreCase) == true)
            .Where(asset => !ReferenceEquals(asset, mission) && asset.Name.Contains(Path.GetFileNameWithoutExtension(mission.Name), StringComparison.OrdinalIgnoreCase)))
        {
            AddAsset(files, seen, root, names, companion, "mission-companion", "Same mission directory; binary semantics remain evidence-only.", cancellationToken);
        }

        foreach (GameAsset camera in library.Cameras
            .Where(asset => asset.Name.Contains("find_blunderbuss", StringComparison.OrdinalIgnoreCase)
                || asset.InternalPath.Contains("find_blunderbuss", StringComparison.OrdinalIgnoreCase)))
            AddAsset(files, seen, root, names, camera, "camera", "Mission-name match; camera transform/timing schema is not fully decoded.", cancellationToken);

        foreach (GameAsset animation in library.AnimationSets
            .Where(asset => asset.Name.Contains("dlc_find_blunderbuss", StringComparison.OrdinalIgnoreCase)
                || asset.Name.Contains("blacksmith", StringComparison.OrdinalIgnoreCase)))
            AddAsset(files, seen, root, names, animation, "animation", "Name match; WAS records are independently indexed by the animation library.", cancellationToken);

        foreach (GameAsset audio in library.Audio
            .Where(asset => asset.Name.Contains("blunderbuss", StringComparison.OrdinalIgnoreCase)))
            AddAsset(files, seen, root, names, audio, "audio", "Name match; AWC members can be decoded separately by awc_extract.py.", cancellationToken);

        // The mission payload does not expose a validated world-cell ID yet.
        // DLC_Find_Blunderbuss is therefore packaged with the Armadillo
        // territory candidate set, not presented as proven runtime placement.
        // Include every WBD/WVD in the territory archive so WSI hashes can
        // resolve across the whole cell rather than only the blacksmith.
        string territoryArchive = library.Archives.FirstOrDefault(path =>
            Path.GetFileName(path).Equals("armadillo.rpf", StringComparison.OrdinalIgnoreCase)) ?? "";
        if (territoryArchive.Length > 0)
        {
            using Rpf6Archive archive = Rpf6Archive.Open(territoryArchive, names);
            foreach (Rpf6Archive.Entry entry in archive.Entries.Where(entry =>
                !entry.IsDirectory &&
                entry.Path.Contains("root/armadillo/", StringComparison.OrdinalIgnoreCase) &&
                (entry.Name.EndsWith(".wbd", StringComparison.OrdinalIgnoreCase) || entry.Name.EndsWith(".wvd", StringComparison.OrdinalIgnoreCase) || entry.Name.EndsWith(".wsi", StringComparison.OrdinalIgnoreCase))))
            {
                cancellationToken.ThrowIfCancellationRequested();
                string key = territoryArchive + "|" + entry.Index;
                if (!seen.Add(key)) continue;
                byte[] bytes = archive.Extract(entry);
                string relative = SafeRelativePath("world-cell", entry.Name);
                string destination = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
                Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                File.WriteAllBytes(destination, bytes);
                files.Add(new EvidenceFile(
                    entry.Name.EndsWith(".wbd", StringComparison.OrdinalIgnoreCase) ? "world-cell-metadata" : entry.Name.EndsWith(".wsi", StringComparison.OrdinalIgnoreCase) ? "world-cell-placement" : "world-cell-geometry",
                    entry.Name,
                    relative,
                    territoryArchive,
                    entry.Path,
                    bytes.Length,
                    Convert.ToHexString(SHA256.HashData(bytes)),
                    "raw-evidence",
                    "Armadillo blacksmith candidate cell; WBD/WVD/WSI structure is decoded by the bounded inspector and mission-to-cell identity remains unverified."));
            }
        }

        string manifestPath = Path.Combine(root, "world-cell.manifest.json");
        var manifest = new
        {
            format = "rdr1-world-cell-evidence-v0.27",
            mission = new
            {
                query = missionQuery,
                name = mission.Name,
                archive = mission.ArchivePath,
                internalPath = mission.InternalPath,
                status = "mission resource identified; world-cell reference unresolved"
            },
            candidateCell = new
            {
                name = "Armadillo territory / root/armadillo",
                archive = territoryArchive,
                selectionBasis = "territory archive companion set for the DLC_Find_Blunderbuss showcase; not a validated mission placement",
                status = territoryArchive.Length == 0 ? "not-found" : "raw assets extracted"
            },
            counts = new
            {
                total = files.Count,
                mission = files.Count(file => file.Role.StartsWith("mission", StringComparison.Ordinal)),
                camera = files.Count(file => file.Role == "camera"),
                animation = files.Count(file => file.Role == "animation"),
                audio = files.Count(file => file.Role == "audio"),
                worldCell = files.Count(file => file.Role.StartsWith("world-cell", StringComparison.Ordinal))
            },
            files,
            unresolved = new[]
            {
                "mission VM world-cell reference and runtime branch are not decoded",
                "WVD shader/material bindings remain unresolved",
                "WSI secondary drawable-instance records and room/portal relationships remain unresolved",
                "collision/terrain/nav data and streamed-cell ownership remain unresolved",
                "camera transform values are evidence-only until their parent/coordinate schema is validated"
            }
        };
        File.WriteAllText(manifestPath, JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true }));
        File.WriteAllText(Path.Combine(root, "README.txt"),
            "RDR1 Animation Studio v0.25 world-cell evidence\r\n\r\n" +
            "This folder contains byte-preserved inputs for one mission and a named candidate world cell. " +
            "WBD reports include validated bounds and quantized collision vertices; WVD reports include validated drawable LOD/model/geometry headers, vertex declarations, positions, and indices; WSI reports include pointer-validated sector placement matrices and instance hashes. " +
            "It is not yet a reconstructed game world. The manifest records hashes, archive paths, and every unresolved boundary.\r\n");
        return manifestPath;
    }

    private static void AddAsset(
        List<EvidenceFile> files,
        HashSet<string> seen,
        string root,
        IReadOnlyDictionary<uint, string> names,
        GameAsset asset,
        string role,
        string notes,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        string key = asset.ArchivePath + "|" + asset.EntryIndex;
        if (!seen.Add(key)) return;
        byte[] bytes = GameLibrary.Extract(asset, names);
        string relative = SafeRelativePath(role, asset.Name);
        string destination = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
        if (File.Exists(destination))
        {
            string suffix = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(asset.ArchivePath + "|" + asset.EntryIndex)))[..8].ToLowerInvariant();
            relative = SafeRelativePath(role, Path.GetFileNameWithoutExtension(asset.Name) + "-" + suffix + Path.GetExtension(asset.Name));
            destination = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
        }
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        File.WriteAllBytes(destination, bytes);
        files.Add(new EvidenceFile(role, asset.Name, relative, asset.ArchivePath, asset.InternalPath,
            bytes.Length, Convert.ToHexString(SHA256.HashData(bytes)), "extracted", notes));
    }

    private static string SafeRelativePath(string folder, string name)
    {
        string safe = string.Concat(name.Select(ch => char.IsLetterOrDigit(ch) || ch is '.' or '-' or '_' ? ch : '_'));
        return folder + "/" + (safe.Length == 0 ? "unnamed.bin" : safe);
    }
}
