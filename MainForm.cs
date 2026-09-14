using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using RDR1AnimationStudio.Core;

namespace RDR1AnimationStudio;

internal sealed class MainForm : Form
{
    private readonly Panel body = new() { Dock = DockStyle.Fill, Padding = new Padding(26) };
    private readonly Label title = new() { Text = "Animation library", Dock = DockStyle.Top, Height = 55, Font = new Font("Segoe UI", 24, FontStyle.Bold) };
    private readonly Label summary = new() { Text = "Choose your game folder to collect all animation sets.", Dock = DockStyle.Top, Height = 32 };
    private readonly Label status = new() { Text = "RDR1 Animation Studio / eSaudia", Dock = DockStyle.Bottom, Height = 36, Padding = new Padding(18, 8, 0, 0) };
    private readonly TextBox search = new() { PlaceholderText = "Search animation names or source sets…", Width = 400 };
    private readonly CheckBox unique = new() { Text = "Hide identical motion", Checked = true, AutoSize = true };
    private readonly CheckBox supported = new() { Text = "Supported only", AutoSize = true };
    private readonly ComboBox character = new() { Width = 280, DropDownStyle = ComboBoxStyle.DropDownList };
    private readonly DataGridView grid = new() { Dock = DockStyle.Fill, AutoGenerateColumns = false, ReadOnly = true, AllowUserToAddRows = false, AllowUserToDeleteRows = false, RowHeadersVisible = false, MultiSelect = true, SelectionMode = DataGridViewSelectionMode.FullRowSelect, BorderStyle = BorderStyle.None, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill };
    private readonly Panel libraryPage = new() { Dock = DockStyle.Fill };
    private readonly Panel infoPage = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly RichTextBox info = new() { Dock = DockStyle.Fill, ReadOnly = true, BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 12) };
    private readonly List<Clip> clips = [];
    private readonly List<SourceFile> sources = [];
    private readonly List<string> problems = [];
    private readonly List<CharacterChoice> characters = [];
    private IReadOnlyDictionary<uint, string> archiveNames = new Dictionary<uint, string>();
    private bool dark = true, busy, ascending = true;
    private CancellationTokenSource? operation;
    private string? blender;
    private readonly AnimationPreview preview = new() { Dock = DockStyle.Fill };
    private readonly System.Windows.Forms.Timer previewTimer = new() { Interval = 450 };
    private CancellationTokenSource? previewOperation;
    private int previewVersion;
    private static string ToolPath => Path.Combine(AppContext.BaseDirectory, "Tools", "WasInspector");
    internal sealed record SourceFile(string File, string Label);
    internal sealed record Clip(string Source, string Set, int Offset, string Name, int Frames, double Fps, double Duration, string Fingerprint, string Status, string DuplicateOf) {
        public string Availability => Status == "Supported" ? (DuplicateOf.Length > 0 ? "Identical motion" : "Supported") : "Needs decoder work";
        public string Identity => Source + "@" + Offset;
        // Preserve the game's resource name exactly.  Names such as test3374616257_4
        // are real internal clip identifiers; replacing them with a fabricated label
        // made it impossible to match an animation back to the game.
        public string DisplayName => Name;
    }
    private sealed record CharacterChoice(string Label, GameAsset? Asset) {
        public override string ToString() => Label;
    }
    private sealed record Catalog(List<Clip> Clips, List<JsonElement> Errors);

    public MainForm()
    {
        Text = "RDR1 Animation Studio — eSaudia"; Size = new Size(1400, 860); MinimumSize = new Size(1140, 700);
        Font = new Font("Segoe UI", 10); StartPosition = FormStartPosition.CenterScreen;
        Icon = new Icon(Path.Combine(AppContext.BaseDirectory, "Assets", "studio.ico"));
        var nav = new FlowLayoutPanel { Dock = DockStyle.Left, Width = 205, Padding = new Padding(18, 25, 5, 0), FlowDirection = FlowDirection.TopDown, WrapContents = false };
        nav.Controls.Add(new Label { Text = "RDR1\nANIMATION\nSTUDIO", Size = new Size(175, 125), Font = new Font("Segoe UI", 20, FontStyle.Bold) });
        foreach (var (icon, name) in new[] { ("▦", "Library"), ("↥", "Import"), ("≡", "Scan report"), ("ⓘ", "About") }) {
            var button = Button(icon + "   " + name, (_, _) => ShowPage(name)); button.Size = new Size(172, 48); button.AutoSize = false; button.TextAlign = ContentAlignment.MiddleLeft; nav.Controls.Add(button);
        }
        nav.Controls.Add(new Label { Text = "CREATED BY\neSaudia", Size = new Size(170, 60), Margin = new Padding(4, 80, 0, 0), Font = new Font("Segoe UI", 11, FontStyle.Bold) });
        var top = new Panel { Dock = DockStyle.Top, Height = 60 };
        var theme = Button("☼  Theme", (_, _) => { dark = !dark; Theme(); }); theme.Dock = DockStyle.Right;
        top.Controls.Add(title); top.Controls.Add(theme);
        var actions = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 56 };
        actions.Controls.Add(Button("＋ Game folder", async (_, _) => await ChooseGameAsync()));
        actions.Controls.Add(Button("＋ Add WAS", async (_, _) => await AddWasAsync()));
        actions.Controls.Add(Button("✓ Select all", (_, _) => grid.SelectAll()));
        actions.Controls.Add(Button("↗ Export selected", async (_, _) => await ExportAsync(false)));
        actions.Controls.Add(Button("↗ Export all", async (_, _) => await ExportAsync(true)));
        actions.Controls.Add(Button("× Cancel", (_, _) => operation?.Cancel()));
        var filters = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 44 };
        filters.Controls.Add(new Label { Text = "Search", AutoSize = true, Padding = new Padding(0, 3, 5, 0) }); filters.Controls.Add(search);
        filters.Controls.Add(new Label { Text = "Character", AutoSize = true, Padding = new Padding(14, 3, 5, 0) }); filters.Controls.Add(character);
        filters.Controls.Add(unique); filters.Controls.Add(supported);
        search.TextChanged += (_, _) => RefreshGrid(); unique.CheckedChanged += (_, _) => RefreshGrid(); supported.CheckedChanged += (_, _) => RefreshGrid();
        character.SelectedIndexChanged += (_, _) => { previewVersion++; previewTimer.Stop(); previewOperation?.Cancel(); preview.Notice("Select one supported animation to preview."); if (!busy) previewTimer.Start(); };
        SetCharacters([]);
        foreach (var (name, property, weight) in new[] { ("Animation", "DisplayName", 30), ("Source set", "Set", 25), ("Frames", "Frames", 7), ("FPS", "Fps", 5), ("Seconds", "Duration", 7), ("Status", "Availability", 15) })
            grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = name, DataPropertyName = property, FillWeight = weight, SortMode = DataGridViewColumnSortMode.Programmatic });
        grid.RowTemplate.Height = 34; grid.ColumnHeadersHeight = 42;
        grid.Columns[4].DefaultCellStyle.Format = "0.###";
        grid.ColumnHeaderMouseClick += (_, e) => {
            var rows = ((List<Clip>?)grid.DataSource) ?? [];
            Func<Clip, object> key = e.ColumnIndex switch { 0 => c => c.Name, 1 => c => c.Set, 2 => c => c.Frames, 3 => c => c.Fps, 4 => c => c.Duration, _ => c => c.Availability };
            grid.DataSource = (ascending ? rows.OrderBy(key) : rows.OrderByDescending(key)).ToList(); ascending = !ascending;
        };
        grid.SelectionChanged += (_, _) => { if (!busy && grid.SelectedRows.Count == 1 && grid.SelectedRows[0].DataBoundItem is Clip c) status.Text = c.Name + " · " + c.Status + " · Record 0x" + c.Offset.ToString("X") + (c.DuplicateOf.Length > 0 ? " · Same motion: " + c.DuplicateOf : ""); };
        grid.SelectionChanged += (_, _) => { previewVersion++; previewTimer.Stop(); previewOperation?.Cancel(); preview.Notice("Select one supported animation to preview."); if (!busy) previewTimer.Start(); };
        previewTimer.Tick += async (_, _) => { previewTimer.Stop(); await PreviewSelectedAsync(); };
        var split = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal, Size = new Size(1000, 650), SplitterDistance = 320, Panel1MinSize = 140, Panel2MinSize = 200 };
        split.Panel1.Controls.Add(grid); split.Panel2.Controls.Add(preview);
        libraryPage.Controls.Add(split); libraryPage.Controls.Add(filters); libraryPage.Controls.Add(actions);
        infoPage.Controls.Add(info);
        body.Controls.Add(libraryPage); body.Controls.Add(infoPage); body.Controls.Add(summary); body.Controls.Add(top);
        Controls.Add(body); Controls.Add(nav); Controls.Add(status);
        FormClosing += (_, e) => { if (busy) { operation?.Cancel(); e.Cancel = true; status.Text = "Cancelling… close again when stopped."; } };
        Theme();
        FormClosed += (_, _) => { previewTimer.Stop(); previewTimer.Dispose(); previewOperation?.Cancel(); };
    }
    private static Button Button(string label, EventHandler click) {
        string text = label[(label.IndexOf(' ') + 1)..].Trim();
        var b = new Button { Text = text, Image = DrawIcon(text), TextImageRelation = TextImageRelation.ImageBeforeText, ImageAlign = ContentAlignment.MiddleLeft, AutoSize = true, Height = 38, FlatStyle = FlatStyle.Flat, Padding = new Padding(8, 5, 8, 5), Margin = new Padding(0, 0, 7, 8), Cursor = Cursors.Hand };
        b.FlatAppearance.BorderSize = 0; b.Click += click; return b;
    }
    private static Bitmap DrawIcon(string name)
    {
        var image = new Bitmap(24, 24);
        using var g = Graphics.FromImage(image); g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        using var p = new Pen(Color.FromArgb(83, 196, 191), 1.8f);
        if (name.Contains("Library")) { for (int x=4;x<20;x+=9) for(int y=4;y<20;y+=9) g.DrawRectangle(p,x,y,6,6); }
        else if (name.Contains("Import") || name.Contains("Export")) { g.DrawLine(p,4,17,4,21);g.DrawLine(p,4,21,20,21);g.DrawLine(p,20,21,20,17);g.DrawLine(p,12,17,12,4);g.DrawLine(p,12,4,7,9);g.DrawLine(p,12,4,17,9); }
        else if (name.Contains("folder") || name.Contains("WAS")) { g.DrawLines(p,[new Point(3,19),new Point(3,5),new Point(10,5),new Point(13,8),new Point(21,8),new Point(21,19),new Point(3,19)]); }
        else if (name.Contains("Select")) { g.DrawEllipse(p,3,3,18,18);g.DrawLines(p,[new Point(7,12),new Point(10,15),new Point(17,8)]); }
        else if (name.Contains("Cancel")) { g.DrawLine(p,6,6,18,18);g.DrawLine(p,18,6,6,18); }
        else if (name.Contains("report")) { g.DrawRectangle(p,5,3,14,18); for(int y=7;y<19;y+=5) g.DrawLine(p,8,y,16,y); }
        else { g.DrawEllipse(p,3,3,18,18);g.DrawLine(p,12,10,12,17);g.DrawEllipse(p,11,6,2,2); }
        return image;
    }
    private void SetCharacters(IEnumerable<GameAsset> discovered)
    {
        characters.Clear();
        characters.Add(new CharacterChoice("John · bundled human rig", null));
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var asset in discovered.Where(a => a.Name.EndsWith(".wft", StringComparison.OrdinalIgnoreCase)))
        {
            string shortName = Path.GetFileNameWithoutExtension(asset.Name);
            if (shortName.Length == 0 || !seen.Add(shortName)) continue;
            string label = shortName.Replace('_', ' ');
            characters.Add(new CharacterChoice(label + " · game model", asset));
        }
        characters.Sort((a, b) => StringComparer.OrdinalIgnoreCase.Compare(a.Label, b.Label));
        character.DataSource = null;
        character.Items.Clear();
        foreach (var item in characters) character.Items.Add(item);
        int bundled = characters.FindIndex(item => item.Asset is null);
        if (bundled >= 0) character.SelectedIndex = bundled;
        character.AccessibleName = "Preview character";
        character.AccessibleDescription = "Choose the character mesh used for the animation preview";
    }
    private CharacterChoice SelectedCharacter => character.SelectedItem as CharacterChoice ?? characters[0];
    internal void SaveSnapshot(string output, string? catalogPath)
    {
        if (catalogPath is not null) { var c = JsonSerializer.Deserialize<Catalog>(File.ReadAllText(catalogPath)); if (c is not null) clips.AddRange(c.Clips); }
        ShowInTaskbar = false; StartPosition = FormStartPosition.Manual; Location = new Point(-20000, -20000); Show(); Application.DoEvents();
        CreateControl();
        void Create(Control c) { c.CreateControl(); foreach (Control child in c.Controls) Create(child); }
        Create(this); RefreshGrid(); PerformLayout();
        using var bitmap = new Bitmap(Width, Height); DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(output); Hide();
    }
    private void Theme() {
        Color bg = dark ? Color.FromArgb(19, 23, 30) : Color.FromArgb(244, 246, 249), fg = dark ? Color.FromArgb(225, 231, 239) : Color.FromArgb(26, 32, 43);
        void Apply(Control c) { c.BackColor = c is Button ? (dark ? Color.FromArgb(37, 47, 61) : Color.FromArgb(220, 230, 241)) : bg; c.ForeColor = fg; foreach (Control child in c.Controls) Apply(child); }
        Apply(this); grid.BackgroundColor = bg; grid.EnableHeadersVisualStyles = false;
        grid.DefaultCellStyle.BackColor = bg; grid.DefaultCellStyle.ForeColor = fg; grid.DefaultCellStyle.SelectionBackColor = Color.FromArgb(36, 85, 108); grid.DefaultCellStyle.SelectionForeColor = Color.White;
        grid.AlternatingRowsDefaultCellStyle.BackColor = dark ? Color.FromArgb(25, 30, 39) : Color.White;
        grid.ColumnHeadersDefaultCellStyle.BackColor = dark ? Color.FromArgb(31, 39, 50) : Color.FromArgb(222, 231, 241); grid.ColumnHeadersDefaultCellStyle.ForeColor = fg; grid.GridColor = dark ? Color.FromArgb(38, 45, 56) : Color.LightGray;
    }
    private void ShowPage(string page) {
        title.Text = page == "Library" ? "Animation library" : page;
        libraryPage.Visible = page == "Library"; infoPage.Visible = !libraryPage.Visible;
        infoPage.Controls.Clear(); infoPage.Controls.Add(info);
        if (page == "About") info.Text = "RDR1 ANIMATION STUDIO\n\nCreated by eSaudia\nCreator & Project Owner\n\nVersion 0.10 · Animation Lab\n\nGame-wide animation discovery, character-aware preview, Blender export and experimental WAS import.\n\nBlender is used as an external editor. Python-zstandard license is included in the distribution.\n\nAnimation names are read from game resources. Unknown names are not invented.";
        else if (page == "Scan report") info.Text = "SCAN REPORT\n\n" + string.Join("\n", problems) + "\n\n" + string.Join("\n", clips.Where(c => c.Status != "Supported").Select(c => c.Set + " / " + c.Name + ": " + c.Status));
        else if (page == "Import") {
            info.Text = "BLENDER → WAS\n\nSelect one supported clip in Library, then choose its edited Blender file below.\n\nExperimental import retains the original duration, skeleton and storage layout. Changes that cannot be represented safely are rejected with an explanation.\n\nThe output is a new WAS file with a validation report. Installation into game archives is not automatic.\n";
            var b = Button("↥ Import edited Blender animation…", async (_, _) => await ImportAsync()); b.Dock = DockStyle.Bottom; infoPage.Controls.Add(b);
        }
        Theme();
    }
    private void RefreshGrid() {
        var selected = grid.SelectedRows.Cast<DataGridViewRow>().Select(r => (r.DataBoundItem as Clip)?.Identity).ToHashSet(); string query = search.Text.Trim();
        grid.DataSource = clips.Where(c => (!unique.Checked || c.DuplicateOf.Length == 0) && (!supported.Checked || c.Status == "Supported") && (query.Length == 0 || c.Name.Contains(query, StringComparison.OrdinalIgnoreCase) || c.Set.Contains(query, StringComparison.OrdinalIgnoreCase))).ToList();
        grid.ClearSelection(); foreach (DataGridViewRow row in grid.Rows) if (row.DataBoundItem is Clip c && selected.Contains(c.Identity)) row.Selected = true;
        summary.Text = $"{clips.Count:N0} clips · {clips.Count(c => c.Status == "Supported"):N0} supported · {clips.Count(c => c.DuplicateOf.Length > 0):N0} identical · {grid.Rows.Count:N0} shown";
    }
    private async Task Operation(Func<CancellationToken, Task> action) {
        if (busy) return; busy = true; previewTimer.Stop(); previewVersion++; previewOperation?.Cancel(); operation = new();
        try { await action(operation.Token); } catch (OperationCanceledException) { status.Text = "Cancelled. Completed files retained."; }
        catch (Exception ex) { status.Text = "Operation failed"; MessageBox.Show(this, ex.Message, "RDR1 Animation Studio", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        finally { busy = false; operation.Dispose(); operation = null; }
    }
    private async Task ChooseGameAsync() {
        if (busy) return;
        using var dialog = new FolderBrowserDialog { Description = "Choose Red Dead Redemption or its game folder", UseDescriptionForTitle = true };
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token => {
            var path = Path.Combine(AppContext.BaseDirectory, "Assets", "ImportedFileNames.txt"); var names = File.Exists(path) ? Rpf6Archive.LoadNames(path) : new Dictionary<uint, string>();
            archiveNames = names;
            IProgress<string> progress = new Progress<string>(s => status.Text = s);
            var game = await GameLibrary.ScanAsync(dialog.SelectedPath, names, progress, token); var input = new List<SourceFile>();
            string cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "Library"); Directory.CreateDirectory(cache);
            var errors = new List<string>(game.Errors);
            await Task.Run(() => { foreach (var group in game.AnimationSets.GroupBy(a => a.ArchivePath)) {
                token.ThrowIfCancellationRequested(); using var archive = Rpf6Archive.Open(group.Key, names);
                foreach (var asset in group) { token.ThrowIfCancellationRequested(); try {
                    byte[] bytes = archive.Extract(archive.Entries[asset.EntryIndex]); string file = Path.Combine(cache, Convert.ToHexString(SHA256.HashData(bytes)) + ".was");
                    if (!File.Exists(file)) File.WriteAllBytes(file, bytes);
                    input.Add(new(file, Path.GetRelativePath(dialog.SelectedPath, group.Key) + "/" + asset.InternalPath));
                } catch (Exception ex) { errors.Add(asset.InternalPath + ": " + ex.Message); } }
                progress.Report($"Collecting {input.Count:N0}/{game.AnimationSets.Count:N0} animation sets…");
            } }, token);
            await IndexAsync(input, token); problems.AddRange(errors);
            SetCharacters(game.Characters);
            status.Text = $"Collection complete · {input.Count:N0} animation sets · {characters.Count:N0} preview characters.";
        });
    }
    private async Task AddWasAsync() {
        if (busy) return; using var d = new OpenFileDialog { Title = "Add animation sets", Filter = "WAS|*.was", Multiselect = true }; if (d.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token => await IndexAsync(sources.Concat(d.FileNames.Select(f => new SourceFile(f, Path.GetFileName(f)))).Distinct().ToList(), token));
    }
    private async Task IndexAsync(List<SourceFile> input, CancellationToken token) {
        string job = Job(), manifest = Path.Combine(job, "sources.json"), output = Path.Combine(job, "catalog.json");
        await File.WriteAllTextAsync(manifest, JsonSerializer.Serialize(input), token); await Python("studio_library.py", ["index", manifest, output], token);
        var result = JsonSerializer.Deserialize<Catalog>(await File.ReadAllTextAsync(output, token)) ?? throw new InvalidDataException("Empty catalog");
        clips.Clear(); clips.AddRange(result.Clips); sources.Clear(); sources.AddRange(input); problems.Clear(); problems.AddRange(result.Errors.Select(e => e.ToString()));
        RefreshGrid(); status.Text = "Collection complete. Unsupported animations are explained in Scan report.";
    }
    private async Task ExportAsync(bool all) {
        if (busy) return;
        var selected = all ? clips.ToList() : grid.SelectedRows.Cast<DataGridViewRow>().Select(r => (Clip)r.DataBoundItem!).ToList();
        if (selected.Count == 0) { status.Text = "Select clips or scan a game folder first."; return; }
        string? exe = Blender(); if (exe is null) return;
        using var d = new FolderBrowserDialog { Description = "Choose Blender export folder", UseDescriptionForTitle = true }; if (d.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token => {
            string manifest = Path.Combine(Job(), "selection.json"), output = Path.Combine(d.SelectedPath, "RDR1-Export-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + "-" + Guid.NewGuid().ToString("N")[..6]);
            await File.WriteAllTextAsync(manifest, JsonSerializer.Serialize(selected), token);
            await Python("studio_library.py", ["export", manifest, output, "--blender", exe, "--skeleton", Path.Combine(AppContext.BaseDirectory, "Assets", "player_default.skeleton.csv"), "--base-blend", Path.Combine(AppContext.BaseDirectory, "Assets", "player_default.blend")], token);
            status.Text = "Export complete. Review export-report.json for skipped clips.";
            MessageBox.Show(this, "Saved Blender Actions and export report:\n" + output + "\n\nUnsupported clips are reported without substituting another animation.", "Export complete");
        });
    }
    private async Task PreviewSelectedAsync()
    {
        if (busy || grid.SelectedRows.Count != 1 || grid.SelectedRows[0].DataBoundItem is not Clip clip) return;
        if (clip.Status != "Supported") { preview.Notice("This clip cannot be decoded reliably yet.\n" + clip.Status); return; }
        CharacterChoice selectedCharacter = SelectedCharacter;
        string? exe = Blender(); if (exe is null) { preview.Notice("Blender is required to prepare the character preview."); return; }
        int version = previewVersion;
        using var cancellation = new CancellationTokenSource(); previewOperation = cancellation;
        var token = cancellation.Token;
        try {
            preview.Notice("Preparing " + selectedCharacter.Label + "…\nFirst preview requires Blender conversion; repeat previews are cached.");
            string assets = Path.Combine(AppContext.BaseDirectory, "Assets");
            string characterKey = selectedCharacter.Asset is null ? "john-bundled" : selectedCharacter.Asset.ArchivePath + "|" + selectedCharacter.Asset.InternalPath + "|" + selectedCharacter.Asset.EntryIndex;
            string key = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes("v010-character|" + characterKey + "|" + clip.Identity + "|" + clip.Fingerprint)));
            string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "Preview", key);
            Directory.CreateDirectory(folder); string model = Path.Combine(folder, "preview.glb");
            if (!File.Exists(model)) {
                string request = Path.Combine(folder, "selection.json");
                await File.WriteAllTextAsync(request, JsonSerializer.Serialize(new[] { clip }), token);
                await Python("studio_library.py", ["export", request, folder, "--blender", exe, "--skeleton", Path.Combine(assets, "player_default.skeleton.csv"), "--base-blend", Path.Combine(assets, "player_default.blend")], token);
                using var report = JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(folder, "export-report.json"), token));
                string blend = report.RootElement.GetProperty("exported")[0].GetProperty("file").GetString()!;
                string pending = Path.Combine(folder, "pending.glb");
                if (selectedCharacter.Asset is null)
                    await Execute(exe, ["--background", blend, "--python-exit-code", "1", "--python", Path.Combine(ToolPath, "blender_preview_glb.py"), "--", pending], token);
                else
                {
                    string sourceGltf = Path.ChangeExtension(blend, ".gltf");
                    if (!File.Exists(sourceGltf)) throw new FileNotFoundException("The animation glTF interchange file was not produced.", sourceGltf);
                    string geometry = await PrepareCharacterGeometryAsync(selectedCharacter.Asset, token);
                    string merged = Path.Combine(folder, "character.gltf");
                    await Python("wft_gltf_merge.py", [sourceGltf, geometry, "--output", merged], token);
                    await Execute(exe, ["--background", "--factory-startup", "--python-exit-code", "1", "--python", Path.Combine(ToolPath, "blender_gltf_to_glb.py"), "--", merged, pending], token);
                }
                token.ThrowIfCancellationRequested(); File.Move(pending, model, true);
            }
            if (version != previewVersion || IsDisposed) return;
            await preview.LoadModelAsync(model, clip.DisplayName + " · " + selectedCharacter.Label);
            await preview.Loaded.Task.WaitAsync(TimeSpan.FromSeconds(30), token);
            status.Text = "Source name: " + clip.Name + " · " + clip.Set + " · Preview: " + selectedCharacter.Label + " · Mouse drag: orbit, wheel: zoom";
        } catch (OperationCanceledException) { }
        catch (Exception ex) { if (version == previewVersion && !IsDisposed) preview.Notice("Cannot preview this clip on " + selectedCharacter.Label + ".\n" + ex.Message[..Math.Min(ex.Message.Length, 600)]); }
        finally { if (ReferenceEquals(previewOperation, cancellation)) previewOperation = null; }
    }
    private async Task<string> PrepareCharacterGeometryAsync(GameAsset asset, CancellationToken token)
    {
        string cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "Characters");
        Directory.CreateDirectory(cache);
        string key = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(asset.ArchivePath + "|" + asset.InternalPath + "|" + asset.EntryIndex + "|" + asset.Size)));
        string resource = Path.Combine(cache, key + ".wft"), geometry = Path.Combine(cache, key + ".geometry.json");
        if (!File.Exists(resource)) await File.WriteAllBytesAsync(resource, GameLibrary.Extract(asset, archiveNames), token);
        if (!File.Exists(geometry)) await Python("wft_geometry_inspector.py", [resource, "--output", geometry], token);
        return geometry;
    }
    private async Task ImportAsync() {
        if (busy) return;
        if (grid.SelectedRows.Count != 1 || grid.SelectedRows[0].DataBoundItem is not Clip clip || clip.Status != "Supported") { MessageBox.Show(this, "Select one supported clip in Library first."); return; }
        string? exe = Blender(); if (exe is null) return;
        using var open = new OpenFileDialog { Title = "Edited Blender file exported by this app", Filter = "Blender|*.blend" }; if (open.ShowDialog(this) != DialogResult.OK) return;
        using var save = new SaveFileDialog { Title = "Save a new edited WAS", Filter = "WAS|*.was", FileName = clip.Name + ".edited.was" }; if (save.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token => {
            string job = Job(), request = Path.Combine(job, "clip.json"), samples = Path.Combine(job, "edited.json");
            await File.WriteAllTextAsync(request, JsonSerializer.Serialize(clip), token);
            await Execute(exe, ["--background", "--factory-startup", "--python-exit-code", "1", "--python", Path.Combine(ToolPath, "blender_read_action.py"), "--", open.FileName, request, samples, Path.Combine(AppContext.BaseDirectory, "Assets", "player_default.skeleton.csv")], token);
            await Python("was_import.py", [request, samples, save.FileName], token);
            MessageBox.Show(this, "Edited WAS and validation report saved. Game-loader acceptance still requires an in-game test. No game archive was changed.\n\n" + save.FileName, "Import complete");
        });
    }
    private string? Blender() {
        if (blender is not null && File.Exists(blender)) return blender;
        string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Blender Foundation");
        blender = Directory.Exists(root) ? Directory.GetFiles(root, "blender.exe", SearchOption.AllDirectories).OrderDescending().FirstOrDefault() : null;
        if (blender is not null) return blender;
        using var d = new OpenFileDialog { Title = "Locate Blender", Filter = "Blender|blender.exe" }; return d.ShowDialog(this) == DialogResult.OK ? blender = d.FileName : null;
    }
    private static string Job() { string p = Path.Combine(Path.GetTempPath(), "RDR1AnimationStudio", Guid.NewGuid().ToString("N")); Directory.CreateDirectory(p); return p; }
    private Task Python(string script, string[] args, CancellationToken token) => Execute(FindWorkingPython() ?? throw new FileNotFoundException("Python 3.12 is required by the animation engine."), new[] { Path.Combine(ToolPath, script) }.Concat(args).ToArray(), token, true);
    private async Task Execute(string exe, string[] args, CancellationToken token, bool python = false) {
        var start = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true }; foreach (string arg in args) start.ArgumentList.Add(arg);
        if (python) start.Environment["PYTHONPATH"] = Path.Combine(ToolPath, "_deps");
        using var p = Process.Start(start) ?? throw new InvalidOperationException("Could not start animation engine");
        using var registration = token.Register(() => { try { p.Kill(true); } catch { } });
        var errors = p.StandardError.ReadToEndAsync(); var output = new System.Text.StringBuilder();
        while (await p.StandardOutput.ReadLineAsync(token) is { } line) { output.AppendLine(line); status.Text = line.Length > 180 ? line[..180] : line; }
        await p.WaitForExitAsync(token); string error = await errors; if (p.ExitCode != 0) throw new InvalidOperationException(error + "\n" + output);
    }
    internal static string? FindWorkingPython() {
        foreach (string root in new[] { Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".cache", "codex-runtimes"), Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python") })
            if (Directory.Exists(root)) foreach (string path in Directory.EnumerateFiles(root, "python.exe", SearchOption.AllDirectories)) { try {
                using var p = Process.Start(new ProcessStartInfo(path, "--version") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true });
                if (p is not null && p.WaitForExit(3000) && p.StandardOutput.ReadToEnd().StartsWith("Python 3.12")) return path;
            } catch { } }
        return null;
    }
}
