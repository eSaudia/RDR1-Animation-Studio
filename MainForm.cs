using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
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
    private readonly Panel packPage = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly Panel scenePage = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly Panel infoPage = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly RichTextBox info = new() { Dock = DockStyle.Fill, ReadOnly = true, BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 12) };
    private readonly RichTextBox packInfo = new() { Dock = DockStyle.Fill, ReadOnly = true, BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 12) };
    private readonly TextBox authorName = new() { Width = 260, Text = "custom_action" };
    private readonly NumericUpDown authorFrames = new() { Width = 90, Minimum = 2, Maximum = 10000, Value = 90 };
    private readonly NumericUpDown authorFps = new() { Width = 90, Minimum = 1, Maximum = 240, Value = 30 };
    private readonly ComboBox authorPreset = new() { Width = 220, DropDownStyle = ComboBoxStyle.DropDownList };
    private readonly TextBox packName = new() { Width = 260, Text = "My RDR1 Animation Pack" };
    private readonly ComboBox packCharacter = new() { Width = 280, DropDownStyle = ComboBoxStyle.DropDownList };
    private string? lastPackFolder;
    private readonly List<Clip> clips = [];
    private readonly List<SourceFile> sources = [];
    private readonly List<string> problems = [];
    private readonly List<CharacterChoice> characters = [];
    private GameLibrary? gameLibrary;
    private IReadOnlyDictionary<uint, string> archiveNames = new Dictionary<uint, string>();
    private bool dark = true, busy, ascending = true, closeRequested;
    private CancellationTokenSource? operation;
    private string? blender;
    private readonly AnimationPreview preview = new() { Dock = DockStyle.Fill };
    private readonly System.Windows.Forms.Timer previewTimer = new() { Interval = 450 };
    private CancellationTokenSource? previewOperation;
    private int previewVersion;
    private readonly ListBox sceneResourceList = new() { Dock = DockStyle.Fill, IntegralHeight = false, HorizontalScrollbar = true, FormattingEnabled = true };
    private readonly TextBox sceneSearch = new() { Width = 300, PlaceholderText = "Search mission or camera resources…" };
    private readonly ComboBox sceneFilter = new() { Width = 150, DropDownStyle = ComboBoxStyle.DropDownList };
    private readonly Label sceneDetails = new() { Dock = DockStyle.Top, AutoSize = false, Height = 122, Padding = new Padding(10), TextAlign = ContentAlignment.TopLeft };
    private readonly Panel sceneTimeline = new() { Dock = DockStyle.Fill, BorderStyle = BorderStyle.FixedSingle };
    private readonly TrackBar sceneScrubber = new() { Minimum = 0, Maximum = 1, TickStyle = TickStyle.None, AutoSize = false, Height = 28 };
    private readonly Label sceneClock = new() { AutoSize = true, Padding = new Padding(6, 7, 8, 0), Text = "Evidence 0 / 0" };
    private readonly Button scenePlay = new() { Text = "▶ Play evidence", Width = 140, Height = 34, FlatStyle = FlatStyle.Flat };
    private readonly System.Windows.Forms.Timer scenePlaybackTimer = new() { Interval = 80 };
    private CancellationTokenSource? sceneLoadOperation;
    private IReadOnlyList<GameAsset> sceneResources = [];
    private List<CutsceneMarker> sceneMarkers = [];
    private int sceneCursor;
    private bool scenePlaying;
    // Python discovery used to recursively scan every runtime directory on
    // every operation.  Keep the result for this process so a second decode
    // starts immediately, and probe it off the UI thread where needed.
    private static readonly object pythonProbeLock = new();
    private static bool pythonProbeComplete;
    private static string? pythonPath;
    private static readonly Dictionary<string, bool> pythonDependencyFallback = new(StringComparer.OrdinalIgnoreCase);
    private static string ToolPath => Path.Combine(AppContext.BaseDirectory, "Tools", "WasInspector");
    internal sealed record SourceFile(string File, string Label);
    private sealed record ArchiveStamp(string Path, long Length, long LastWriteUtcTicks);
    private sealed record CollectionCache(string Root, int AnimationSetCount, List<ArchiveStamp> Archives, List<SourceFile> Sources);
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
    private sealed record CutsceneMarker(string Track, string Label, int Sequence, int Offset, string Confidence);
    private sealed record Catalog(List<Clip> Clips, List<JsonElement> Errors);

    public MainForm()
    {
        Text = "RDR1 Animation Studio — eSaudia"; Size = new Size(1400, 860); MinimumSize = new Size(1140, 700);
        Font = new Font("Segoe UI", 10); StartPosition = FormStartPosition.CenterScreen;
        Icon = new Icon(Path.Combine(AppContext.BaseDirectory, "Assets", "studio.ico"));
        authorPreset.Items.AddRange(["Blank authoring pose", "Spider leap (demo)"]); authorPreset.SelectedIndex = 0;
        var nav = new FlowLayoutPanel { Dock = DockStyle.Left, Width = 205, Padding = new Padding(18, 25, 5, 0), FlowDirection = FlowDirection.TopDown, WrapContents = false };
        nav.Controls.Add(new Label { Text = "RDR1\nANIMATION\nSTUDIO", Size = new Size(175, 125), Font = new Font("Segoe UI", 20, FontStyle.Bold) });
        foreach (var (icon, name) in new[] { ("▦", "Library"), ("▤", "Scenes"), ("✦", "Authoring"), ("◈", "Packs"), ("↥", "Import"), ("≡", "Scan report"), ("ⓘ", "About") }) {
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
        BuildPackPage();
        BuildScenePage();
        body.Controls.Add(libraryPage); body.Controls.Add(scenePage); body.Controls.Add(packPage); body.Controls.Add(infoPage); body.Controls.Add(summary); body.Controls.Add(top);
        Controls.Add(body); Controls.Add(nav); Controls.Add(status);
        FormClosing += (_, e) =>
        {
            if (!busy || closeRequested) return;
            closeRequested = true;
            e.Cancel = true;
            operation?.Cancel();
            status.Text = "Cancelling operation and closing…";
            _ = CloseWhenIdleAsync();
        };
        Theme();
        FormClosed += (_, _) => { previewTimer.Stop(); previewTimer.Dispose(); scenePlaybackTimer.Stop(); scenePlaybackTimer.Dispose(); previewOperation?.Cancel(); sceneLoadOperation?.Cancel(); };
    }
    private async Task CloseWhenIdleAsync()
    {
        try
        {
            while (busy) await Task.Delay(50);
            if (!IsDisposed && IsHandleCreated) BeginInvoke(new Action(Close));
        }
        catch (ObjectDisposedException) { }
        catch (InvalidOperationException) { }
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
        else if (name.Contains("Authoring")) { g.DrawLine(p,5,19,19,5); g.DrawLine(p,5,5,19,19); g.DrawEllipse(p,9,9,6,6); }
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
        packCharacter.Items.Clear();
        foreach (var item in characters) packCharacter.Items.Add(item);
        packCharacter.SelectedIndex = bundled >= 0 ? bundled : (packCharacter.Items.Count > 0 ? 0 : -1);
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
        libraryPage.Visible = page == "Library"; scenePage.Visible = page == "Scenes"; packPage.Visible = page == "Packs"; infoPage.Visible = !libraryPage.Visible && !scenePage.Visible && !packPage.Visible;
        infoPage.Controls.Clear(); infoPage.Controls.Add(info);
        if (page == "About") info.Text = "RDR1 ANIMATION STUDIO\n\nCreated by eSaudia\nCreator & Project Owner\n\nVersion 0.16 · Scene resource decoding\n\nGame-wide animation discovery, character-aware preview, Blender export and experimental WAS import.\n\nScene selections now use the full semantic decoder when available, exposing bounded VM traces, RBF labels, XML attributes, and conservative track candidates; unknown payloads remain unresolved.\n\nBlender is used as an external editor. Python-zstandard license is included in the distribution.\n\nAnimation names are read from game resources. Unknown names are not invented.";
        else if (page == "Scan report") {
            string inventory = gameLibrary is null ? "No game folder scanned yet." :
                $"Mission resource inventory (identified, not decoded):\n" +
                $"  Scripts: {gameLibrary.MissionScripts.Count:N0}\n" +
                $"  Cameras: {gameLibrary.Cameras.Count:N0}\n" +
                $"  Props: {gameLibrary.Props.Count:N0}\n" +
                $"  AI data: {gameLibrary.AiData.Count:N0}\n" +
                $"  Audio: {gameLibrary.Audio.Count:N0}\n\n" +
                $"Native database: {(FindNativeDatabase() is string nativeDb ? nativeDb : "not configured (raw hashes will be retained)")}\n\n" +
                "These counts are a safe inventory only; binary mission decoding is not silently substituted.\n\n";
            info.Text = "SCAN REPORT\n\n" + inventory + string.Join("\n", problems) + "\n\n" + string.Join("\n", clips.Where(c => c.Status != "Supported").Select(c => c.Set + " / " + c.Name + ": " + c.Status));
            var decode = Button("◈ Decode resource semantics…", async (_, _) => await DecodeResourceAsync()); decode.Dock = DockStyle.Bottom; infoPage.Controls.Add(decode);
            var oneMission = Button("▣ Decode one mission package…", async (_, _) => await DecodeOneMissionAsync()); oneMission.Dock = DockStyle.Bottom; infoPage.Controls.Add(oneMission);
            var vmIndex = Button("▣ Build VM header index…", async (_, _) => await BuildVmIndexAsync()); vmIndex.Dock = DockStyle.Bottom; infoPage.Controls.Add(vmIndex);
            var importNative = Button("⌘ Import native database…", async (_, _) => await ImportNativeDatabaseAsync()); importNative.Dock = DockStyle.Bottom; infoPage.Controls.Add(importNative);
            var usage = Button("▤ Explore native usage…", async (_, _) => await ExploreNativeUsageAsync()); usage.Dock = DockStyle.Bottom; infoPage.Controls.Add(usage);
            var scripts = Button("▥ Explore VM scripts…", async (_, _) => await ExploreVmScriptsAsync()); scripts.Dock = DockStyle.Bottom; infoPage.Controls.Add(scripts);
            var json = Button("▤ Open JSON report…", async (_, _) => await OpenJsonViewerAsync()); json.Dock = DockStyle.Bottom; infoPage.Controls.Add(json);
        }
        else if (page == "Import") {
            info.Text = "BLENDER → WAS\n\nSelect one supported clip in Library, then choose its edited Blender file below.\n\nExperimental import retains the original duration, skeleton and storage layout. Changes that cannot be represented safely are rejected with an explanation.\n\nThe output is a new WAS file with a validation report. Installation into game archives is not automatic.\n";
            var b = Button("↥ Import edited Blender animation…", async (_, _) => await ImportAsync()); b.Dock = DockStyle.Bottom; infoPage.Controls.Add(b);
        }
        else if (page == "Authoring") BuildAuthoringPage();
        Theme();
    }
    private async Task DecodeResourceAsync()
    {
        using var open = new OpenFileDialog { Title = "Select an extracted RDR1 resource", Filter = "All files|*.*|Cutscene/XML|*.bin;*.xml" };
        if (open.ShowDialog(this) != DialogResult.OK) return;
        using var save = new SaveFileDialog { Title = "Save semantic report", Filter = "JSON report|*.json", FileName = Path.GetFileNameWithoutExtension(open.FileName) + ".semantics.json" };
        if (save.ShowDialog(this) != DialogResult.OK) return;
        try
        {
            // Prefer the bundled Python helper when available: it can expand Zstandard
            // RSC payloads (including mission resources) before the native fallback.
            string? python = await Task.Run(FindWorkingPython);
            string semanticScript = Path.Combine(ToolPath, "resource_semantics.py");
            if (python is not null && File.Exists(semanticScript))
            {
                try
                {
                    var semanticArgs = new List<string> { semanticScript, open.FileName, "--output", save.FileName };
                    string? nativeDb = FindNativeDatabase();
                    if (nativeDb is not null) { semanticArgs.Add("--native-db"); semanticArgs.Add(nativeDb); }
                    await Execute(python, semanticArgs.ToArray(), CancellationToken.None, true);
                    status.Text = "Semantic report written · " + Path.GetFileName(save.FileName);
                    if (MessageBox.Show(this, "Decoded verified resource semantics to:\n\n" + save.FileName + "\n\nUnknown fields are marked unresolved instead of being guessed.\n\nOpen it in the Raw / Structured JSON viewer now?", "Semantic decode complete", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                        ShowJsonReport(save.FileName);
                    return;
                }
                catch { /* Native decoder below still provides a useful report. */ }
            }
            byte[] bytes = await File.ReadAllBytesAsync(open.FileName);
            string json = ResourceSemantics.DecodeToJson(bytes, Path.GetFullPath(open.FileName));
            await File.WriteAllTextAsync(save.FileName, json + Environment.NewLine);
            status.Text = "Semantic report written · " + Path.GetFileName(save.FileName);
            if (MessageBox.Show(this, "Decoded verified resource semantics to:\n\n" + save.FileName + "\n\nUnknown fields are marked unresolved instead of being guessed.\n\nOpen it in the Raw / Structured JSON viewer now?", "Semantic decode complete", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                ShowJsonReport(save.FileName);
        }
        catch (Exception ex) { MessageBox.Show(this, "Could not decode resource:\n" + ex.Message, "Semantic decode", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
    private async Task BuildVmIndexAsync()
    {
        if (busy) return;
        if (gameLibrary is null || gameLibrary.MissionScripts.Count == 0)
        {
            MessageBox.Show(this, "Choose a game folder first so the mission resource inventory is available.", "VM header index");
            return;
        }
        using var save = new SaveFileDialog { Title = "Save mission VM index", Filter = "JSON report|*.json", FileName = "rdr1-mission-vm-index.json", AddExtension = true, OverwritePrompt = true };
        if (save.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token =>
        {
            string? python = await Task.Run(FindWorkingPython, token);
            string batchScript = Path.Combine(ToolPath, "mission_vm_batch.py");
            if (python is null || !File.Exists(batchScript)) throw new FileNotFoundException("Python 3.12 is required for the VM indexer.");
            string? nativeDb = FindNativeDatabase();
            string tempRoot = Path.Combine(Path.GetTempPath(), "RDR1AnimationStudio-vm-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempRoot);
            try
            {
                var manifest = new List<Dictionary<string, object?>>();
                int completed = 0;
                foreach (GameAsset asset in gameLibrary.MissionScripts)
                {
                    token.ThrowIfCancellationRequested();
                    byte[] bytes = GameLibrary.Extract(asset, archiveNames);
                    string file = Path.Combine(tempRoot, asset.EntryIndex.ToString(System.Globalization.CultureInfo.InvariantCulture) + ".bin");
                    await File.WriteAllBytesAsync(file, bytes, token);
                    manifest.Add(new Dictionary<string, object?> { ["file"] = file, ["name"] = asset.Name, ["archive"] = asset.ArchivePath, ["internalPath"] = asset.InternalPath, ["entryIndex"] = asset.EntryIndex });
                    completed++;
                    status.Text = $"Preparing VM index · {completed:N0}/{gameLibrary.MissionScripts.Count:N0}";
                }
                string manifestPath = Path.Combine(tempRoot, "manifest.json");
                await File.WriteAllTextAsync(manifestPath, JsonSerializer.Serialize(manifest), token);
                var batchArgs = new List<string> { batchScript, manifestPath, save.FileName };
                if (nativeDb is not null) { batchArgs.Add("--native-db"); batchArgs.Add(nativeDb); }
                await Execute(python, batchArgs.ToArray(), token, true);
                status.Text = "VM index written · " + Path.GetFileName(save.FileName);
                if (MessageBox.Show(this, "Mission VM index written to:\n\n" + save.FileName + "\n\nOnly validated script headers are counted as decoded.\n\nOpen it in the Raw / Structured JSON viewer now?", "VM index complete", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                    ShowJsonReport(save.FileName);
            }
            finally
            {
                try { Directory.Delete(tempRoot, true); } catch { }
            }
        });
    }
    private async Task DecodeOneMissionAsync()
    {
        if (busy) return;
        if (gameLibrary is null || gameLibrary.MissionScripts.Count == 0)
        {
            MessageBox.Show(this, "Choose a game folder first so the mission resource inventory is available.", "Single mission decode");
            return;
        }

        var candidates = gameLibrary.MissionScripts
            .OrderBy(asset => asset.Name, StringComparer.OrdinalIgnoreCase)
            .ThenBy(asset => asset.InternalPath, StringComparer.OrdinalIgnoreCase)
            .ToList();
        using var picker = new Form { Text = "Choose one mission resource", Size = new Size(1120, 640), MinimumSize = new Size(760, 440), StartPosition = FormStartPosition.CenterParent };
        var filter = new TextBox { Width = 420, Height = 30, PlaceholderText = "Filter by name, archive, or internal path…" };
        var group = new ComboBox { Width = 170, DropDownStyle = ComboBoxStyle.DropDownList };
        group.Items.AddRange(["All scripts", "Mission folders", "DLC scripts", "Common scripts", "Item / brain scripts", "Other"]);
        group.SelectedIndex = 0;
        var sort = new ComboBox { Width = 120, DropDownStyle = ComboBoxStyle.DropDownList };
        sort.Items.AddRange(["Name", "Path", "Size"]); sort.SelectedIndex = 0;
        var toolbar = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 44, WrapContents = false, Padding = new Padding(8, 7, 8, 0) };
        toolbar.Controls.Add(new Label { Text = "Search", AutoSize = true, Padding = new Padding(0, 5, 4, 0) }); toolbar.Controls.Add(filter);
        toolbar.Controls.Add(new Label { Text = "Group", AutoSize = true, Padding = new Padding(14, 5, 4, 0) }); toolbar.Controls.Add(group);
        toolbar.Controls.Add(new Label { Text = "Sort", AutoSize = true, Padding = new Padding(14, 5, 4, 0) }); toolbar.Controls.Add(sort);
        var list = new ListBox { Dock = DockStyle.Fill, IntegralHeight = false, HorizontalScrollbar = true, FormattingEnabled = true };
        var footer = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 52, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(0, 7, 8, 0) };
        var decode = Button("✓ Decode selected mission", (_, _) =>
        {
            if (list.SelectedItem is null) { MessageBox.Show(picker, "Select one mission resource first.", "Single mission decode"); return; }
            picker.DialogResult = DialogResult.OK;
        });
        var cancel = Button("× Cancel", (_, _) => picker.DialogResult = DialogResult.Cancel);
        footer.Controls.Add(cancel); footer.Controls.Add(decode);
        void RefreshChoices()
        {
            string query = filter.Text.Trim();
            string selectedGroup = group.SelectedItem as string ?? "All scripts";
            static string Group(GameAsset asset)
            {
                string path = (asset.Name + "/" + asset.InternalPath).ToLowerInvariant();
                if (path.Contains("/missions/") || path.Contains("\\missions\\") || path.Contains("mission")) return "Mission folders";
                if (path.Contains("/dlc/") || path.Contains("\\dlc\\")) return "DLC scripts";
                if (path.Contains("commonscripts")) return "Common scripts";
                if (path.Contains("itemscripts") || path.Contains("gringobrainscripts")) return "Item / brain scripts";
                return "Other";
            }
            IEnumerable<GameAsset> filtered = candidates.Where(asset =>
                (query.Length == 0 || asset.Name.Contains(query, StringComparison.OrdinalIgnoreCase) || asset.ArchivePath.Contains(query, StringComparison.OrdinalIgnoreCase) || asset.InternalPath.Contains(query, StringComparison.OrdinalIgnoreCase)) &&
                (selectedGroup == "All scripts" || Group(asset) == selectedGroup));
            var visible = (sort.SelectedItem as string) switch
            {
                "Path" => filtered.OrderBy(asset => asset.InternalPath, StringComparer.OrdinalIgnoreCase).ThenBy(asset => asset.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
                "Size" => filtered.OrderByDescending(asset => asset.Size).ThenBy(asset => asset.Name, StringComparer.OrdinalIgnoreCase).ToArray(),
                _ => filtered.OrderBy(asset => asset.Name, StringComparer.OrdinalIgnoreCase).ThenBy(asset => asset.InternalPath, StringComparer.OrdinalIgnoreCase).ToArray()
            };
            // Keep the asset object as the selected item, while formatting a useful
            // human-readable row. Binding DisplayMember directly hides the name and
            // archive context, which makes it too easy to select the wrong script.
            list.DataSource = null;
            list.Items.Clear();
            list.Items.AddRange(visible);
            if (visible.Length > 0) list.SelectedIndex = 0;
        }
        filter.TextChanged += (_, _) => RefreshChoices(); group.SelectedIndexChanged += (_, _) => RefreshChoices(); sort.SelectedIndexChanged += (_, _) => RefreshChoices();
        list.Format += (_, e) =>
        {
            if (e.ListItem is GameAsset asset)
                e.Value = $"{asset.Name}  ·  {Path.GetFileName(asset.ArchivePath)}  ·  {asset.InternalPath}  ·  {asset.Size:N0} bytes";
        };
        picker.Controls.Add(list); picker.Controls.Add(toolbar); picker.Controls.Add(footer); picker.AcceptButton = decode; picker.CancelButton = cancel; ThemeDialog(picker); RefreshChoices();
        if (picker.ShowDialog(this) != DialogResult.OK || list.SelectedItem is not GameAsset assetToDecode) return;

        using var save = new SaveFileDialog { Title = "Save single-mission semantic report", Filter = "JSON report|*.json", FileName = Path.GetFileNameWithoutExtension(assetToDecode.Name) + ".mission.semantics.json", AddExtension = true, OverwritePrompt = true };
        if (save.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token =>
        {
            status.Text = "Locating Python decoder…";
            string? python = await Task.Run(FindWorkingPython, token);
            string semanticScript = Path.Combine(ToolPath, "resource_semantics.py");
            byte[] bytes = GameLibrary.Extract(assetToDecode, archiveNames);
            byte[]? expandedPayload = null;
            if (python is not null && File.Exists(semanticScript))
            {
                string tempRoot = Path.Combine(Path.GetTempPath(), "RDR1AnimationStudio-single-vm-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(tempRoot);
                try
                {
                    string resource = Path.Combine(tempRoot, "mission.bin");
                    string expandedFile = Path.Combine(tempRoot, "mission.expanded.bin");
                    await File.WriteAllBytesAsync(resource, bytes, token);
                    var args = new List<string> { semanticScript, resource, "--output", save.FileName, "--expanded-output", expandedFile };
                    string? nativeDb = FindNativeDatabase();
                    if (nativeDb is not null) { args.Add("--native-db"); args.Add(nativeDb); }
                    await Execute(python, args.ToArray(), token, true);
                    if (File.Exists(expandedFile)) expandedPayload = await File.ReadAllBytesAsync(expandedFile, token);
                }
                finally { try { Directory.Delete(tempRoot, true); } catch { } }
            }
            else
            {
                // The native reader still writes a truthful structural report when
                // Python/zstandard is unavailable; compressed payload details remain
                // marked unresolved instead of making the operation look like a no-op.
                string json = ResourceSemantics.DecodeToJson(bytes, assetToDecode.InternalPath);
                await File.WriteAllTextAsync(save.FileName, json + Environment.NewLine, token);
            }
            if (!File.Exists(save.FileName)) throw new IOException("The decoder completed without creating the selected report file.");
            string packageDirectory = Path.Combine(Path.GetDirectoryName(save.FileName)!, Path.GetFileNameWithoutExtension(assetToDecode.Name) + ".mission-package-" + DateTime.Now.ToString("yyyyMMdd-HHmmss"));
            string manifestPath = MissionPackage.Write(packageDirectory, bytes, assetToDecode.Name, assetToDecode.ArchivePath, assetToDecode.InternalPath, save.FileName, expandedPayload);
            status.Text = "Single mission package written · " + Path.GetFileName(packageDirectory);
            FileInfo report = new(save.FileName);
            if (MessageBox.Show(this, $"Single mission package created.\n\nManifest:\n{manifestPath}\n\nSource bytes, encrypted/decrypted payload previews, expanded payload (when available), manifest, and semantic report were saved together.\nThe report separates validated container/header/bytecode evidence from unresolved mission semantics; no other mission was scanned.\n\nOpen the semantic report in the Raw / Structured JSON viewer now?", "Single mission package complete", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                ShowJsonReport(report.FullName);
        });
    }
    private async Task OpenJsonViewerAsync()
    {
        using var open = new OpenFileDialog { Title = "Open a RDR1 Animation Studio JSON report", Filter = "JSON reports|*.json|All files|*.*", CheckFileExists = true };
        if (open.ShowDialog(this) != DialogResult.OK) return;
        try
        {
            // A semantics report can legitimately be gigabytes (for example a
            // complete audio bank).  ReadAllText + JsonDocument would allocate
            // several copies and crash the process, so switch to a bounded
            // streaming viewer for large files.
            const long largeJsonThreshold = 64L * 1024 * 1024;
            if (new FileInfo(open.FileName).Length > largeJsonThreshold) ShowLargeJsonViewer(open.FileName);
            else ShowJsonViewer(open.FileName);
        }
        catch (Exception ex) { MessageBox.Show(this, "Could not open JSON report:\n" + ex.Message, "JSON viewer", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        await Task.CompletedTask;
    }
    private void ShowJsonReport(string path)
    {
        try
        {
            const long largeJsonThreshold = 64L * 1024 * 1024;
            if (!File.Exists(path)) throw new FileNotFoundException("The report file was not found.", path);
            if (new FileInfo(path).Length > largeJsonThreshold) ShowLargeJsonViewer(path); else ShowJsonViewer(path);
        }
        catch (Exception ex) { MessageBox.Show(this, "Could not open JSON report:\n" + ex.Message, "JSON viewer", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
    private void ShowLargeJsonViewer(string path)
    {
        const int chunkSize = 512 * 1024;
        var file = new FileInfo(path);
        using var dialog = new Form { Text = "JSON report · " + Path.GetFileName(path) + " (large file mode)", Size = new Size(1200, 780), MinimumSize = new Size(820, 520), StartPosition = FormStartPosition.CenterParent };
        var tabs = new TabControl { Dock = DockStyle.Fill };
        var rawPage = new TabPage("Raw preview");
        var rawBox = new RichTextBox { Dock = DockStyle.Fill, ReadOnly = true, DetectUrls = false, Font = new Font("Cascadia Mono", 10), WordWrap = false, BackColor = Color.FromArgb(22, 26, 33), ForeColor = Color.FromArgb(220, 226, 235), BorderStyle = BorderStyle.None };
        var controls = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 42, WrapContents = false, Padding = new Padding(8, 6, 8, 0) };
        var position = new NumericUpDown { Minimum = 0, Maximum = Math.Max(0, file.Length - 1), Width = 150, ThousandsSeparator = true };
        var read = Button("Read 512 KB", (_, _) => LoadChunk(position.Value));
        var first = Button("First", (_, _) => { position.Value = 0; LoadChunk(0); });
        var lastOffset = Math.Max(0, file.Length - chunkSize);
        var last = Button("Last", (_, _) => { position.Value = lastOffset; LoadChunk(lastOffset); });
        controls.Controls.Add(new Label { Text = "Offset", AutoSize = true, Padding = new Padding(0, 5, 4, 0) }); controls.Controls.Add(position); controls.Controls.Add(read); controls.Controls.Add(first); controls.Controls.Add(last);
        rawPage.Controls.Add(rawBox); rawPage.Controls.Add(controls);
        var structuredPage = new TabPage("Structured summary");
        var summaryBox = new RichTextBox { Dock = DockStyle.Fill, ReadOnly = true, BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 11), BackColor = Color.FromArgb(22, 26, 33), ForeColor = Color.FromArgb(220, 226, 235), Text =
            "Large JSON safe mode\n\n" +
            $"File: {file.Name}\nSize: {file.Length:N0} bytes ({file.Length / 1024d / 1024d:N1} MB)\n\n" +
            "The complete tree is intentionally not materialized because doing so would consume more memory than the report itself.\n\n" +
            "Use Raw preview to inspect any 512 KB window. Smaller reports still use the full syntax-colored raw view and expandable structured tree." };
        structuredPage.Controls.Add(summaryBox); tabs.TabPages.Add(rawPage); tabs.TabPages.Add(structuredPage);
        var footer = new Label { Dock = DockStyle.Bottom, Height = 30, Text = Path.GetFullPath(path) + "  ·  large-file streaming mode", Padding = new Padding(8, 6, 0, 0), AutoEllipsis = true };
        dialog.Controls.Add(tabs); dialog.Controls.Add(footer); ThemeDialog(dialog);
        void LoadChunk(decimal requested)
        {
            long offset = Math.Clamp((long)requested, 0, Math.Max(0, file.Length - 1));
            try
            {
                byte[] bytes = new byte[Math.Min(chunkSize, (int)Math.Min(int.MaxValue, file.Length - offset))];
                using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
                stream.Seek(offset, SeekOrigin.Begin); int readCount = stream.Read(bytes, 0, bytes.Length);
                rawBox.Text = Encoding.UTF8.GetString(bytes, 0, readCount);
                HighlightJson(rawBox);
                dialog.Text = $"JSON report · {file.Name} · offset {offset:N0}";
            }
            catch (Exception ex) { rawBox.Text = "Could not read this section: " + ex.Message; }
        }
        LoadChunk(0); dialog.ShowDialog(this);
    }
    private void ShowJsonViewer(string path)
    {
        string raw = File.ReadAllText(path);
        using JsonDocument document = JsonDocument.Parse(raw);
        string pretty = JsonSerializer.Serialize(document.RootElement, new JsonSerializerOptions { WriteIndented = true });
        using var dialog = new Form { Text = "JSON report · " + Path.GetFileName(path), Size = new Size(1200, 780), MinimumSize = new Size(820, 520), StartPosition = FormStartPosition.CenterParent };
        var tabs = new TabControl { Dock = DockStyle.Fill };
        var rawPage = new TabPage("Raw JSON");
        var rawBox = new RichTextBox { Dock = DockStyle.Fill, ReadOnly = true, DetectUrls = false, Font = new Font("Cascadia Mono", 10), WordWrap = false, BackColor = Color.FromArgb(22, 26, 33), ForeColor = Color.FromArgb(220, 226, 235), BorderStyle = BorderStyle.None, Text = pretty };
        HighlightJson(rawBox);
        rawPage.Controls.Add(rawBox);
        var treePage = new TabPage("Structured view");
        var tree = new TreeView { Dock = DockStyle.Fill, HideSelection = false, BorderStyle = BorderStyle.None, Font = new Font("Segoe UI", 10) };
        tree.Nodes.Add(JsonTreeNode("root", document.RootElement)); tree.Nodes[0].Expand(); treePage.Controls.Add(tree);
        tabs.TabPages.Add(rawPage); tabs.TabPages.Add(treePage);
        var footer = new Label { Dock = DockStyle.Bottom, Height = 30, Text = Path.GetFullPath(path) + "  ·  " + new FileInfo(path).Length.ToString("N0") + " bytes", Padding = new Padding(8, 6, 0, 0), AutoEllipsis = true };
        dialog.Controls.Add(tabs); dialog.Controls.Add(footer); ThemeDialog(dialog); dialog.ShowDialog(this);
    }
    private static TreeNode JsonTreeNode(string label, JsonElement value)
    {
        static string Short(string text) => text.Length > 180 ? text[..177] + "…" : text;
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                var obj = new TreeNode(label + "  {");
                foreach (var property in value.EnumerateObject()) obj.Nodes.Add(JsonTreeNode(property.Name, property.Value));
                obj.Text += "  [" + obj.Nodes.Count.ToString(System.Globalization.CultureInfo.InvariantCulture) + " fields]";
                return obj;
            case JsonValueKind.Array:
                var array = new TreeNode(label + "  [" + value.GetArrayLength().ToString(System.Globalization.CultureInfo.InvariantCulture) + " items]");
                int index = 0; foreach (var item in value.EnumerateArray()) array.Nodes.Add(JsonTreeNode("[" + index++.ToString(System.Globalization.CultureInfo.InvariantCulture) + "]", item));
                return array;
            case JsonValueKind.String: return new TreeNode(label + ":  \"" + Short(value.GetString() ?? "") + "\"");
            case JsonValueKind.Number: return new TreeNode(label + ":  " + value.GetRawText());
            case JsonValueKind.True: return new TreeNode(label + ":  true");
            case JsonValueKind.False: return new TreeNode(label + ":  false");
            case JsonValueKind.Null: return new TreeNode(label + ":  null");
            default: return new TreeNode(label);
        }
    }
    private static void HighlightJson(RichTextBox box)
    {
        // Keep very large reports responsive; they remain fully readable as raw JSON.
        if (box.TextLength > 1_000_000) return;
        string text = box.Text; box.SelectAll(); box.SelectionColor = Color.FromArgb(220, 226, 235);
        int i = 0;
        while (i < text.Length)
        {
            if (text[i] == '"')
            {
                int start = i++; bool escaped = false;
                while (i < text.Length) { char c = text[i++]; if (c == '"' && !escaped) break; escaped = c == '\\' && !escaped ? true : false; if (c != '\\') escaped = false; }
                int next = i; while (next < text.Length && char.IsWhiteSpace(text[next])) next++;
                box.Select(start, i - start); box.SelectionColor = next < text.Length && text[next] == ':' ? Color.FromArgb(120, 190, 255) : Color.FromArgb(150, 220, 150);
                continue;
            }
            if (char.IsDigit(text[i]) || (text[i] == '-' && i + 1 < text.Length && char.IsDigit(text[i + 1])))
            {
                int start = i++; while (i < text.Length && "0123456789+-.eE".Contains(text[i])) i++;
                box.Select(start, i - start); box.SelectionColor = Color.FromArgb(255, 205, 120); continue;
            }
            if (text.AsSpan(i).StartsWith("true")) { box.Select(i, 4); box.SelectionColor = Color.FromArgb(220, 160, 255); i += 4; continue; }
            if (text.AsSpan(i).StartsWith("false")) { box.Select(i, 5); box.SelectionColor = Color.FromArgb(220, 160, 255); i += 5; continue; }
            if (text.AsSpan(i).StartsWith("null")) { box.Select(i, 4); box.SelectionColor = Color.FromArgb(220, 160, 255); i += 4; continue; }
            i++;
        }
        box.Select(0, 0);
    }
    private async Task ImportNativeDatabaseAsync()
    {
        using var open = new OpenFileDialog { Title = "Select an RDR1 native database", Filter = "JSON database|*.json|All files|*.*", CheckFileExists = true };
        if (open.ShowDialog(this) != DialogResult.OK) return;
        try
        {
            FileInfo source = new(open.FileName);
            if (source.Length == 0) throw new InvalidDataException("The selected file is empty.");
            string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio");
            Directory.CreateDirectory(folder);
            string target = Path.Combine(folder, "natives.json");
            File.Copy(source.FullName, target, true);
            status.Text = "Native database imported · " + source.Name;
            MessageBox.Show(this, "Native database imported. Future semantic decodes and VM indexes will resolve matching hashes to names and signatures.\n\nStored at:\n" + target, "Native database", MessageBoxButtons.OK, MessageBoxIcon.Information);
            ShowPage("Scan report");
        }
        catch (Exception ex) { MessageBox.Show(this, "Could not import native database:\n" + ex.Message, "Native database", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        await Task.CompletedTask;
    }
    private sealed record NativeUsageRow(string Hash, string Name, int Count, int Mismatches, int NativeIndex);
    private async Task ExploreNativeUsageAsync()
    {
        using var open = new OpenFileDialog { Title = "Open an archive-wide VM index", Filter = "VM index|*.json|All files|*.*", CheckFileExists = true };
        if (open.ShowDialog(this) != DialogResult.OK) return;
        try
        {
            using JsonDocument document = JsonDocument.Parse(await File.ReadAllTextAsync(open.FileName));
            if (!document.RootElement.TryGetProperty("nativeUsage", out JsonElement entries) || entries.ValueKind != JsonValueKind.Array)
                throw new InvalidDataException("This JSON does not contain an archive-wide nativeUsage table. Build a VM header index first.");
            var rows = entries.EnumerateArray().Select(item => new NativeUsageRow(
                item.TryGetProperty("hash", out var hash) ? hash.GetString() ?? "" : "",
                item.TryGetProperty("name", out var name) ? name.GetString() ?? "(unresolved)" : "(unresolved)",
                item.TryGetProperty("count", out var count) && count.TryGetInt32(out int c) ? c : 0,
                item.TryGetProperty("signatureMismatches", out var mismatches) && mismatches.TryGetInt32(out int m) ? m : 0,
                item.TryGetProperty("nativeIndex", out var index) && index.TryGetInt32(out int i) ? i : -1))
                .Where(row => row.Hash.Length > 0).OrderByDescending(row => row.Count).ThenBy(row => row.Name, StringComparer.OrdinalIgnoreCase).ToList();
            using var dialog = new Form { Text = "RDR1 Native Usage Explorer", Size = new Size(920, 620), MinimumSize = new Size(720, 420), StartPosition = FormStartPosition.CenterParent };
            var filter = new TextBox { Dock = DockStyle.Top, PlaceholderText = "Filter by hash or native name…", Height = 32, Margin = new Padding(8) };
            var table = new DataGridView { Dock = DockStyle.Fill, ReadOnly = true, AllowUserToAddRows = false, RowHeadersVisible = false, AutoGenerateColumns = false, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill, SelectionMode = DataGridViewSelectionMode.FullRowSelect };
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Hash", DataPropertyName = "Hash", FillWeight = 24 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Native name", DataPropertyName = "Name", FillWeight = 44 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Calls", DataPropertyName = "Count", FillWeight = 12 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Sig. mismatches", DataPropertyName = "Mismatches", FillWeight = 15 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Index", DataPropertyName = "NativeIndex", FillWeight = 12 });
            var footer = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 48, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(0, 6, 8, 0) };
            var export = Button("↗ Export CSV…", (_, _) =>
            {
                using var save = new SaveFileDialog { Title = "Export native usage", Filter = "CSV file|*.csv", FileName = "rdr1-native-usage.csv", AddExtension = true, OverwritePrompt = true };
                if (save.ShowDialog(dialog) != DialogResult.OK) return;
                var visible = ((IEnumerable<NativeUsageRow>)table.DataSource!).ToList();
                var csv = new StringBuilder("Hash,Native name,Calls,Signature mismatches,Index\r\n");
                foreach (var row in visible) csv.AppendLine(string.Join(",", Csv(row.Hash), Csv(row.Name), row.Count, row.Mismatches, row.NativeIndex));
                File.WriteAllText(save.FileName, csv.ToString(), new UTF8Encoding(false));
                status.Text = "Native usage CSV written · " + Path.GetFileName(save.FileName);
            });
            var close = Button("× Close", (_, _) => dialog.Close()); footer.Controls.Add(close); footer.Controls.Add(export);
            void ApplyFilter()
            {
                string query = filter.Text.Trim();
                table.DataSource = rows.Where(row => query.Length == 0 || row.Hash.Contains(query, StringComparison.OrdinalIgnoreCase) || row.Name.Contains(query, StringComparison.OrdinalIgnoreCase)).ToList();
            }
            filter.TextChanged += (_, _) => ApplyFilter(); ApplyFilter();
            dialog.Controls.Add(table); dialog.Controls.Add(filter); dialog.Controls.Add(footer); ThemeDialog(dialog);
            dialog.ShowDialog(this);
        }
        catch (Exception ex) { MessageBox.Show(this, "Could not open VM index:\n" + ex.Message, "Native usage", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
    private static string Csv(string value) => "\"" + value.Replace("\"", "\"\"", StringComparison.Ordinal) + "\"";
    private void ThemeDialog(Form dialog)
    {
        Color bg = dark ? Color.FromArgb(19, 23, 30) : Color.FromArgb(244, 246, 249), fg = dark ? Color.FromArgb(225, 231, 239) : Color.FromArgb(26, 32, 43);
        dialog.BackColor = bg; dialog.ForeColor = fg;
        foreach (Control child in dialog.Controls) { child.BackColor = child is Button ? (dark ? Color.FromArgb(37, 47, 61) : Color.FromArgb(220, 230, 241)) : bg; child.ForeColor = fg; }
    }
    private sealed record VmScriptRow(string Name, string Archive, string InternalPath, int Headers, int Functions, int Calls, string Format);
    private async Task ExploreVmScriptsAsync()
    {
        using var open = new OpenFileDialog { Title = "Open an archive-wide VM index", Filter = "VM index|*.json|All files|*.*", CheckFileExists = true };
        if (open.ShowDialog(this) != DialogResult.OK) return;
        try
        {
            using JsonDocument document = JsonDocument.Parse(await File.ReadAllTextAsync(open.FileName));
            if (!document.RootElement.TryGetProperty("resources", out JsonElement entries) || entries.ValueKind != JsonValueKind.Array)
                throw new InvalidDataException("This JSON does not contain a mission resource list. Build a VM header index first.");
            var rows = entries.EnumerateArray().Select(item =>
            {
                string GetString(string property, string fallback = "") => item.TryGetProperty(property, out var value) ? value.GetString() ?? fallback : fallback;
                int GetCount(string property) => item.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Array ? value.GetArrayLength() : 0;
                return new VmScriptRow(GetString("name", "(unnamed)"), GetString("archive"), GetString("internalPath"), GetCount("headers"), item.TryGetProperty("functionCount", out var functions) && functions.TryGetInt32(out int f) ? f : 0, GetCount("callReferences"), GetString("format", "unknown"));
            }).OrderByDescending(row => row.Functions).ThenBy(row => row.Name, StringComparer.OrdinalIgnoreCase).ToList();
            using var dialog = new Form { Text = "RDR1 VM Script Explorer", Size = new Size(1180, 660), MinimumSize = new Size(820, 440), StartPosition = FormStartPosition.CenterParent };
            var filter = new TextBox { Dock = DockStyle.Top, PlaceholderText = "Filter by resource name, archive, or path…", Height = 32, Margin = new Padding(8) };
            var table = new DataGridView { Dock = DockStyle.Fill, ReadOnly = true, AllowUserToAddRows = false, RowHeadersVisible = false, AutoGenerateColumns = false, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill, SelectionMode = DataGridViewSelectionMode.FullRowSelect };
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Resource", DataPropertyName = "Name", FillWeight = 25 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Archive", DataPropertyName = "Archive", FillWeight = 20 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Internal path", DataPropertyName = "InternalPath", FillWeight = 30 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Headers", DataPropertyName = "Headers", FillWeight = 8 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Functions", DataPropertyName = "Functions", FillWeight = 9 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Calls", DataPropertyName = "Calls", FillWeight = 8 });
            table.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Format", DataPropertyName = "Format", FillWeight = 12 });
            var footer = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 48, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(0, 6, 8, 0) };
            var export = Button("↗ Export CSV…", (_, _) =>
            {
                using var save = new SaveFileDialog { Title = "Export VM script inventory", Filter = "CSV file|*.csv", FileName = "rdr1-vm-scripts.csv", AddExtension = true, OverwritePrompt = true };
                if (save.ShowDialog(dialog) != DialogResult.OK) return;
                var visible = ((IEnumerable<VmScriptRow>)table.DataSource!).ToList();
                var csv = new StringBuilder("Resource,Archive,Internal path,Headers,Functions,Calls,Format\r\n");
                foreach (var row in visible) csv.AppendLine(string.Join(",", Csv(row.Name), Csv(row.Archive), Csv(row.InternalPath), row.Headers, row.Functions, row.Calls, Csv(row.Format)));
                File.WriteAllText(save.FileName, csv.ToString(), new UTF8Encoding(false)); status.Text = "VM script CSV written · " + Path.GetFileName(save.FileName);
            });
            var close = Button("× Close", (_, _) => dialog.Close()); footer.Controls.Add(close); footer.Controls.Add(export);
            void ApplyFilter()
            {
                string query = filter.Text.Trim();
                table.DataSource = rows.Where(row => query.Length == 0 || row.Name.Contains(query, StringComparison.OrdinalIgnoreCase) || row.Archive.Contains(query, StringComparison.OrdinalIgnoreCase) || row.InternalPath.Contains(query, StringComparison.OrdinalIgnoreCase)).ToList();
            }
            filter.TextChanged += (_, _) => ApplyFilter(); ApplyFilter();
            dialog.Controls.Add(table); dialog.Controls.Add(filter); dialog.Controls.Add(footer); ThemeDialog(dialog); dialog.ShowDialog(this);
        }
        catch (Exception ex) { MessageBox.Show(this, "Could not open VM index:\n" + ex.Message, "VM scripts", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
    private void BuildAuthoringPage()
    {
        info.Text = "CUSTOM ANIMATION AUTHORING\n\nCreate a clean Blender scene from the bundled RDR1 human rig, then edit the named action in Blender's Dope Sheet or Graph Editor. The scene uses the exact frame count and FPS you choose.\n\nThis is the safe authoring path: it does not overwrite game archives. After editing, export a .blend and use Import for an existing supported WAS clip. Creating a brand-new WAS record is still experimental and is not silently attempted.\n\nCharacter geometry, materials and armature are included in the generated Blender file.";
        var panel = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 150, Padding = new Padding(0, 12, 0, 0), WrapContents = true };
        panel.Controls.Add(new Label { Text = "Action name", AutoSize = true, Padding = new Padding(0, 7, 6, 0) }); panel.Controls.Add(authorName);
        panel.Controls.Add(new Label { Text = "Frames", AutoSize = true, Padding = new Padding(16, 7, 6, 0) }); panel.Controls.Add(authorFrames);
        panel.Controls.Add(new Label { Text = "FPS", AutoSize = true, Padding = new Padding(16, 7, 6, 0) }); panel.Controls.Add(authorFps);
        panel.Controls.Add(new Label { Text = "Preset", AutoSize = true, Padding = new Padding(16, 7, 6, 0) }); panel.Controls.Add(authorPreset);
        var create = Button("✦ Create Blender authoring scene…", async (_, _) => await CreateAuthoringSceneAsync()); create.Width = 260; panel.Controls.Add(create);
        panel.Controls.Add(new Label { Text = "Start with 90 frames at 30 FPS, pose the rig, and add keyframes in Blender.", AutoSize = true, Padding = new Padding(0, 14, 0, 0) });
        infoPage.Controls.Add(panel);
    }
    private async Task CreateAuthoringSceneAsync()
    {
        if (busy) return;
        string name = authorName.Text.Trim();
        if (name.Length == 0 || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0) { MessageBox.Show(this, "Enter a valid action name.", "Custom authoring"); return; }
        string? exe = Blender(); if (exe is null) return;
        using var save = new SaveFileDialog { Title = "Save Blender authoring scene", Filter = "Blender scene|*.blend", FileName = name + ".blend", AddExtension = true, OverwritePrompt = true };
        if (save.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token => {
            string script = Path.Combine(ToolPath, "blender_authoring_setup.py");
            string preset = authorPreset.SelectedIndex == 1 ? "spider_leap" : "blank";
            await Execute(exe, ["--background", "--factory-startup", "--python-exit-code", "1", "--python", script, "--", "--base-blend", Path.Combine(AppContext.BaseDirectory, "Assets", "player_default.blend"), "--output", save.FileName, "--name", name, "--frames", authorFrames.Value.ToString(), "--fps", authorFps.Value.ToString(System.Globalization.CultureInfo.InvariantCulture), "--preset", preset], token);
            status.Text = "Authoring scene created · " + Path.GetFileName(save.FileName);
            if (MessageBox.Show(this, "Authoring scene created. Open it in Blender now?", "Custom authoring", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                Process.Start(new ProcessStartInfo(exe, save.FileName) { UseShellExecute = true });
        });
    }
    private void BuildScenePage()
    {
        var heading = new Label { Dock = DockStyle.Top, Height = 84, Padding = new Padding(0, 4, 0, 0),
            Text = "CUTSCENE VIEWER\n\nBrowse discovered mission/camera resources or open a semantic report. The viewer preserves source order and confidence instead of inventing camera timing.",
            Font = new Font("Segoe UI", 13) };
        sceneFilter.Items.Clear(); sceneFilter.Items.AddRange(["All resources", "Cameras / cutscenes", "Mission scripts"]); sceneFilter.SelectedIndex = 0;
        var browserBar = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 48, WrapContents = false, Padding = new Padding(0, 4, 0, 0) };
        browserBar.Controls.Add(new Label { Text = "Resource", AutoSize = true, Padding = new Padding(0, 7, 5, 0) }); browserBar.Controls.Add(sceneSearch);
        browserBar.Controls.Add(new Label { Text = "Type", AutoSize = true, Padding = new Padding(14, 7, 5, 0) }); browserBar.Controls.Add(sceneFilter);
        var openReport = Button("▤ Open semantic report…", async (_, _) => await OpenCutsceneReportAsync()); openReport.Width = 190; browserBar.Controls.Add(openReport);
        var refresh = Button("↻ Refresh", (_, _) => RefreshSceneResources()); refresh.Width = 110; browserBar.Controls.Add(refresh);
        var left = new Panel { Dock = DockStyle.Fill, Padding = new Padding(0, 8, 10, 0) };
        left.Controls.Add(sceneResourceList);
        var leftCaption = new Label { Dock = DockStyle.Top, Height = 30, Text = "Discovered resources", Padding = new Padding(4, 5, 0, 0) }; left.Controls.Add(leftCaption);
        var right = new Panel { Dock = DockStyle.Fill, Padding = new Padding(10, 8, 0, 0) };
        right.Controls.Add(sceneTimeline); right.Controls.Add(sceneDetails);
        sceneScrubber.Width = 380;
        var playback = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 48, WrapContents = false, Padding = new Padding(0, 6, 0, 0) };
        scenePlay.FlatAppearance.BorderSize = 0; scenePlay.Click += (_, _) => ToggleScenePlayback(); playback.Controls.Add(scenePlay); playback.Controls.Add(sceneScrubber); playback.Controls.Add(sceneClock);
        right.Controls.Add(playback);
        // Give WinForms a design-time width before assigning the divider.  A
        // SplitContainer starts at width 0 during form construction; setting a
        // 350px divider against 240px + 420px minimum panels at that point
        // throws and prevents the whole application from opening.
        var split = new SplitContainer { Dock = DockStyle.Fill, Size = new Size(1000, 500), Orientation = Orientation.Vertical, Panel1MinSize = 240, Panel2MinSize = 420, SplitterDistance = 350 };
        split.Panel1.Controls.Add(left); split.Panel2.Controls.Add(right);
        var footer = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 112, Padding = new Padding(0, 12, 0, 0), WrapContents = true };
        var export = Button("↗ Export sample scene to Blender…", async (_, _) => await ExportSceneSampleAsync()); export.Width = 270;
        var play = Button("▶ Play a Blender scene…", async (_, _) => await PlaySceneAsync()); play.Width = 220;
        var render = Button("● Render scene to MP4…", async (_, _) => await RenderSceneAsync()); render.Width = 220;
        footer.Controls.Add(export);
        footer.Controls.Add(play); footer.Controls.Add(render);
        footer.Controls.Add(new Label { Text = "The lower controls still open or render a Blender scene. Resource tracks above are evidence-only until camera/event payloads are decoded.", AutoSize = true, Padding = new Padding(4, 10, 0, 0) });
        sceneTimeline.Paint += (_, e) => PaintSceneTimeline(e.Graphics, sceneTimeline.ClientSize);
        sceneScrubber.ValueChanged += (_, _) => { sceneCursor = sceneScrubber.Value; UpdateSceneClock(); sceneTimeline.Invalidate(); };
        sceneSearch.TextChanged += (_, _) => RefreshSceneResources(); sceneFilter.SelectedIndexChanged += (_, _) => RefreshSceneResources();
        sceneResourceList.Format += (_, e) => { if (e.ListItem is GameAsset asset) e.Value = $"{asset.Name}  ·  {asset.Kind}"; };
        sceneResourceList.SelectedIndexChanged += async (_, _) => await LoadSelectedSceneResourceAsync();
        scenePlaybackTimer.Tick += (_, _) => AdvanceScenePlayback();
        scenePage.Controls.Add(split); scenePage.Controls.Add(footer); scenePage.Controls.Add(browserBar); scenePage.Controls.Add(heading);
        sceneDetails.Text = "Choose a game folder in Library to populate mission and camera resources.\n\nYou can also open an existing *.semantics.json report directly.";
    }
    private void RefreshSceneResources()
    {
        var all = (gameLibrary?.Cameras ?? []).Concat(gameLibrary?.MissionScripts ?? [])
            .DistinctBy(asset => asset.ArchivePath + "|" + asset.EntryIndex)
            .ToList();
        string query = sceneSearch.Text.Trim();
        string filter = sceneFilter.SelectedItem as string ?? "All resources";
        IEnumerable<GameAsset> visible = all.Where(asset =>
            (query.Length == 0 || asset.Name.Contains(query, StringComparison.OrdinalIgnoreCase) || asset.InternalPath.Contains(query, StringComparison.OrdinalIgnoreCase) || asset.ArchivePath.Contains(query, StringComparison.OrdinalIgnoreCase)) &&
            (filter == "All resources" || (filter == "Cameras / cutscenes" && asset.Kind == GameAssetKind.Camera) || (filter == "Mission scripts" && asset.Kind == GameAssetKind.MissionScript)));
        sceneResources = visible.OrderBy(asset => asset.Name, StringComparer.OrdinalIgnoreCase).ThenBy(asset => asset.InternalPath, StringComparer.OrdinalIgnoreCase).ToArray();
        sceneResourceList.BeginUpdate();
        try
        {
            sceneResourceList.Items.Clear();
            sceneResourceList.Items.AddRange(sceneResources.Cast<object>().ToArray());
            if (sceneResourceList.Items.Count > 0) sceneResourceList.SelectedIndex = 0;
            else
            {
                sceneMarkers = [];
                sceneCursor = 0;
                sceneScrubber.Maximum = 1; sceneScrubber.Value = 0; UpdateSceneClock(); sceneTimeline.Invalidate();
                sceneDetails.Text = gameLibrary is null
                    ? "Choose a game folder in Library to populate mission and camera resources.\n\nYou can also open an existing *.semantics.json report directly."
                    : "No mission or camera resource matches this filter.\n\nTry All resources or open a semantic report directly.";
            }
        }
        finally { sceneResourceList.EndUpdate(); }
    }
    private async Task LoadSelectedSceneResourceAsync()
    {
        if (sceneResourceList.SelectedItem is not GameAsset asset) return;
        sceneLoadOperation?.Cancel();
        using var cancellation = new CancellationTokenSource(); sceneLoadOperation = cancellation;
        try
        {
            sceneDetails.Text = "Reading " + asset.Name + "…\n\nExtracting one resource and decoding its verified semantics; no other mission data is scanned.";
            byte[] bytes = await Task.Run(() => GameLibrary.Extract(asset, archiveNames), cancellation.Token);
            string json = await DecodeSceneResourceAsync(bytes, asset.InternalPath, cancellation.Token);
            using JsonDocument report = JsonDocument.Parse(json);
            ApplyCutsceneReport(report.RootElement, asset.Name, asset.ArchivePath + "\\" + asset.InternalPath);
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { sceneDetails.Text = "Could not read " + asset.Name + ".\n\n" + ex.Message; sceneMarkers = []; sceneCursor = 0; sceneScrubber.Maximum = 1; sceneScrubber.Value = 0; UpdateSceneClock(); sceneTimeline.Invalidate(); }
        finally { if (ReferenceEquals(sceneLoadOperation, cancellation)) sceneLoadOperation = null; }
    }
    private async Task<string> DecodeSceneResourceAsync(byte[] bytes, string source, CancellationToken token)
    {
        // Use the same full semantic engine as the focused mission decoder for
        // scene resources.  It expands supported RSC/Zstandard payloads and
        // exposes bounded VM traces, RBF labels, and XML attributes.  The
        // native reader remains a truthful fallback when Python is unavailable.
        string? python = await Task.Run(FindWorkingPython, token);
        string semanticScript = Path.Combine(ToolPath, "resource_semantics.py");
        if (python is not null && File.Exists(semanticScript))
        {
            string tempRoot = Path.Combine(Path.GetTempPath(), "RDR1AnimationStudio-scene-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempRoot);
            try
            {
                string resource = Path.Combine(tempRoot, "resource.bin");
                string report = Path.Combine(tempRoot, "resource.semantics.json");
                await File.WriteAllBytesAsync(resource, bytes, token);
                var args = new List<string> { semanticScript, resource, "--output", report };
                string? nativeDb = FindNativeDatabase();
                if (nativeDb is not null) { args.Add("--native-db"); args.Add(nativeDb); }
                await Execute(python, args.ToArray(), token, true);
                if (File.Exists(report)) return await File.ReadAllTextAsync(report, token);
            }
            catch (OperationCanceledException) { throw; }
            catch
            {
                // Keep selection useful on systems without the optional Python
                // dependency or for a resource the helper cannot expand.
            }
            finally { try { Directory.Delete(tempRoot, true); } catch { } }
        }
        return await Task.Run(() => ResourceSemantics.DecodeToJson(bytes, source), token);
    }
    private async Task OpenCutsceneReportAsync()
    {
        using var open = new OpenFileDialog { Title = "Open cutscene semantic report", Filter = "Semantic JSON|*.semantics.json;*.mission.semantics.json;*.json|All files|*.*", CheckFileExists = true };
        if (open.ShowDialog(this) != DialogResult.OK) return;
        try
        {
            if (new FileInfo(open.FileName).Length > 128L * 1024 * 1024)
                throw new InvalidDataException("This report is too large for the interactive viewer. Open it in Scan report → JSON viewer instead.");
            sceneDetails.Text = "Loading semantic report…";
            using JsonDocument report = await Task.Run(() => JsonDocument.Parse(File.ReadAllText(open.FileName)), CancellationToken.None);
            ApplyCutsceneReport(report.RootElement, Path.GetFileName(open.FileName), open.FileName);
        }
        catch (Exception ex) { sceneDetails.Text = "Could not open semantic report.\n\n" + ex.Message; }
    }
    private void ApplyCutsceneReport(JsonElement root, string name, string source)
    {
        var markers = new List<CutsceneMarker>();
        JsonElement trace = default;
        if (root.TryGetProperty("missionTrace", out JsonElement directTrace)) trace = directTrace;
        else if (root.TryGetProperty("payload", out JsonElement payload) && payload.TryGetProperty("missionTrace", out JsonElement payloadTrace)) trace = payloadTrace;
        if (trace.ValueKind == JsonValueKind.Object && trace.TryGetProperty("events", out JsonElement events) && events.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement item in events.EnumerateArray().Take(1000))
            {
                string track = TrackFor(item.TryGetProperty("domains", out JsonElement domains) ? domains : default, item.TryGetProperty("kind", out JsonElement kind) ? kind.GetString() : null);
                string label = FirstString(item, "nativeName", "opcode", "functionLabel", "kind") ?? "event";
                int sequence = IntValue(item, "sequence") ?? markers.Count;
                int offset = IntValue(item, "offset") ?? 0;
                markers.Add(new CutsceneMarker(track, label, sequence, offset, "validated VM trace"));
            }
        }
        if (markers.Count == 0 && root.TryGetProperty("typedRecordCandidates", out JsonElement candidates) && candidates.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement item in candidates.EnumerateArray().Take(1000))
            {
                string track = TrackFor(item.TryGetProperty("domains", out JsonElement domains) ? domains : default, item.TryGetProperty("recordType", out JsonElement type) ? type.GetString() : null);
                string label = FirstString(item, "value", "recordType") ?? "record";
                int sequence = markers.Count;
                int offset = IntValue(item, "sourceOffset") ?? 0;
                markers.Add(new CutsceneMarker(track, label, sequence, offset, "lexical candidate"));
            }
        }
        sceneMarkers = markers.OrderBy(marker => marker.Sequence).ThenBy(marker => marker.Offset).ToList();
        sceneCursor = 0;
        sceneScrubber.Maximum = Math.Max(1, sceneMarkers.Count - 1); sceneScrubber.Value = 0;
        string format = FirstString(root, "format", "class") ?? "unknown resource";
        string confidence = FirstString(root, "confidence") ?? "evidence report";
        string timing = sceneMarkers.Any(marker => marker.Confidence == "validated VM trace") ? "VM source-order events" : "source-order candidates (timing unresolved)";
        sceneDetails.Text = $"{name}\n{format} · {sceneMarkers.Count:N0} track events · {timing}\n{source}\n\nConfidence: {confidence}\nSelect an event or press Play evidence. This viewer never converts byte offsets into fake game seconds.";
        UpdateSceneClock(); sceneTimeline.Invalidate();
    }
    private static string TrackFor(JsonElement domains, string? fallback)
    {
        if (domains.ValueKind == JsonValueKind.Array)
            foreach (JsonElement domain in domains.EnumerateArray())
                if (domain.ValueKind == JsonValueKind.String && domain.GetString() is string value && value.Length > 0) return value switch
                {
                    "camera" => "Camera", "actor" => "Actor", "ai" => "AI", "prop" => "Prop", "audio" => "Audio", "animation" => "Animation", "timing" => "Timing", _ => "Other"
                };
        string text = fallback?.ToLowerInvariant() ?? "";
        if (text.Contains("camera") || text.Contains("cut")) return "Camera";
        if (text.Contains("audio") || text.Contains("sound")) return "Audio";
        if (text.Contains("anim")) return "Animation";
        if (text.Contains("ai") || text.Contains("brain")) return "AI";
        return "Other";
    }
    private static string? FirstString(JsonElement item, params string[] properties)
    {
        foreach (string property in properties)
            if (item.TryGetProperty(property, out JsonElement value) && value.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(value.GetString())) return value.GetString();
        return null;
    }
    private static int? IntValue(JsonElement item, string property)
    {
        if (!item.TryGetProperty(property, out JsonElement value)) return null;
        return value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out int result) ? result : null;
    }
    private void ToggleScenePlayback()
    {
        if (sceneMarkers.Count == 0) return;
        scenePlaying = !scenePlaying;
        if (scenePlaying) scenePlaybackTimer.Start(); else scenePlaybackTimer.Stop();
        scenePlay.Text = scenePlaying ? "❚❚ Pause evidence" : "▶ Play evidence";
    }
    private void AdvanceScenePlayback()
    {
        if (!scenePlaying || sceneMarkers.Count == 0) return;
        sceneCursor = sceneCursor >= sceneMarkers.Count - 1 ? 0 : sceneCursor + 1;
        sceneScrubber.Value = Math.Min(sceneScrubber.Maximum, sceneCursor); UpdateSceneClock(); sceneTimeline.Invalidate();
    }
    private void UpdateSceneClock()
    {
        sceneClock.Text = sceneMarkers.Count == 0 ? "Evidence 0 / 0" : $"Evidence {sceneCursor + 1:N0} / {sceneMarkers.Count:N0} · {sceneMarkers[sceneCursor].Track}: {sceneMarkers[sceneCursor].Label}";
    }
    private void PaintSceneTimeline(Graphics graphics, Size size)
    {
        graphics.Clear(dark ? Color.FromArgb(23, 29, 38) : Color.White);
        Color gridLine = dark ? Color.FromArgb(52, 63, 78) : Color.FromArgb(215, 222, 232);
        Color textColor = dark ? Color.FromArgb(220, 228, 238) : Color.FromArgb(35, 42, 52);
        using var pen = new Pen(gridLine); using var brush = new SolidBrush(textColor);
        string[] tracks = ["Camera", "Actor", "AI", "Prop", "Audio", "Animation", "Timing", "Other"];
        int left = 112, top = 48, row = Math.Max(34, (size.Height - top - 12) / tracks.Length), width = Math.Max(1, size.Width - left - 14);
        graphics.DrawString("SOURCE-ORDER EVENT INDEX · NOT GAME SECONDS", Font, brush, 10, 10);
        for (int i = 0; i < tracks.Length; i++)
        {
            int y = top + i * row; graphics.DrawString(tracks[i], Font, brush, 10, y + 9); graphics.DrawLine(pen, left, y + row - 1, left + width, y + row - 1);
        }
        int count = Math.Max(1, sceneMarkers.Count - 1);
        foreach (var (marker, index) in sceneMarkers.Select((item, index) => (item, index)))
        {
            int track = Array.IndexOf(tracks, marker.Track); if (track < 0) track = tracks.Length - 1;
            int x = left + (int)(width * index / (double)count), y = top + track * row + 5;
            int w = sceneMarkers.Count < 80 ? Math.Max(26, width / Math.Max(1, sceneMarkers.Count)) : 8;
            Color fillColor = marker.Track switch { "Camera" => Color.FromArgb(83, 126, 164), "Actor" => Color.FromArgb(40, 143, 126), "AI" => Color.FromArgb(159, 104, 59), "Prop" => Color.FromArgb(128, 104, 160), "Audio" => Color.FromArgb(159, 81, 119), "Animation" => Color.FromArgb(42, 139, 169), "Timing" => Color.FromArgb(133, 133, 74), _ => Color.FromArgb(92, 104, 117) };
            using var fill = new SolidBrush(fillColor); graphics.FillRectangle(fill, x, y, w, Math.Max(18, row - 10));
            if (sceneMarkers.Count < 80) graphics.DrawString(marker.Label.Length > 22 ? marker.Label[..22] + "…" : marker.Label, Font, Brushes.White, x + 4, y + 6);
        }
        if (sceneMarkers.Count > 0)
        {
            int cursorX = left + (int)(width * sceneCursor / (double)count); using var cursorPen = new Pen(Color.Gold, 2); graphics.DrawLine(cursorPen, cursorX, top - 4, cursorX, top + row * tracks.Length);
        }
    }
    private async Task ExportSceneSampleAsync()
    {
        if (busy) return;
        string? exe = Blender(); if (exe is null) return;
        using var save = new SaveFileDialog { Title = "Save sample scene", Filter = "Blender scene|*.blend", FileName = "RDR1-intro-scene-sample.blend", AddExtension = true, OverwritePrompt = true };
        if (save.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token =>
        {
            await Execute(exe, ["--background", "--factory-startup", "--python-exit-code", "1", "--python", Path.Combine(ToolPath, "blender_scene_sample.py"), "--", "--base-blend", Path.Combine(AppContext.BaseDirectory, "Assets", "player_default.blend"), "--output", save.FileName], token);
            status.Text = "Sample scene exported · " + Path.GetFileName(save.FileName);
            if (MessageBox.Show(this, "Sample scene exported. Open it in Blender now?", "Scene exporter", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                LaunchScenePlayback(exe, save.FileName);
        });
    }
    private async Task PlaySceneAsync()
    {
        if (busy) return;
        string? exe = Blender(); if (exe is null) return;
        using var open = new OpenFileDialog { Title = "Open exported Blender scene", Filter = "Blender scenes|*.blend", CheckFileExists = true };
        if (open.ShowDialog(this) != DialogResult.OK) return;
        LaunchScenePlayback(exe, open.FileName);
        status.Text = "Opened scene in Blender and started playback · " + Path.GetFileName(open.FileName);
        await Task.CompletedTask;
    }
    private void LaunchScenePlayback(string exe, string blend)
    {
        string script = Path.Combine(ToolPath, "blender_play_scene.py");
        var start = new ProcessStartInfo(exe) { UseShellExecute = true };
        start.ArgumentList.Add(blend); start.ArgumentList.Add("--python"); start.ArgumentList.Add(script); start.ArgumentList.Add("--"); start.ArgumentList.Add("--start"); start.ArgumentList.Add("1");
        Process.Start(start);
    }
    private async Task RenderSceneAsync()
    {
        if (busy) return;
        string? exe = Blender(); if (exe is null) return;
        using var open = new OpenFileDialog { Title = "Choose Blender scene to render", Filter = "Blender scenes|*.blend", CheckFileExists = true };
        if (open.ShowDialog(this) != DialogResult.OK) return;
        using var save = new SaveFileDialog { Title = "Save rendered scene movie", Filter = "MP4 movie|*.mp4", FileName = Path.GetFileNameWithoutExtension(open.FileName) + ".mp4", AddExtension = true, OverwritePrompt = true };
        if (save.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token =>
        {
            status.Text = "Rendering scene to MP4…";
            await Execute(exe, ["--background", open.FileName, "--python-exit-code", "1", "--python", Path.Combine(ToolPath, "blender_render_scene.py"), "--", "--output", save.FileName], token);
            if (!File.Exists(save.FileName)) throw new IOException("Blender completed without creating the MP4 file.");
            FileInfo movie = new(save.FileName);
            status.Text = $"Scene render complete · {movie.Length:N0} bytes";
            MessageBox.Show(this, $"Scene rendered successfully.\n\n{movie.FullName}\nSize: {movie.Length:N0} bytes", "Scene render complete", MessageBoxButtons.OK, MessageBoxIcon.Information);
        });
    }
    private void BuildPackPage()
    {
        packInfo.Text = "CUSTOM ANIMATION PACKS\n\nCreate a portable, loader-ready pack from the clips selected in Library. Each source WAS is copied once, while the manifest keeps the exact animation name, record offset, character target, frame count and FPS.\n\nThis does not overwrite animationres.rpf. The manifest is the contract a future ScriptHookRDR animation menu can read; the game will not load a loose JSON/WAS folder by itself until that runtime plugin exists.\n\nUse supported clips only. For a new custom motion, first edit an existing exported clip in Blender and import it as a new WAS, then package that result here.";
        var panel = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 170, Padding = new Padding(0, 12, 0, 0), WrapContents = true };
        panel.Controls.Add(new Label { Text = "Pack name", AutoSize = true, Padding = new Padding(0, 7, 6, 0) }); panel.Controls.Add(packName);
        panel.Controls.Add(new Label { Text = "Target character", AutoSize = true, Padding = new Padding(16, 7, 6, 0) }); panel.Controls.Add(packCharacter);
        var create = Button("◈ Create pack from selected clips…", async (_, _) => await CreatePackAsync()); create.Width = 270; panel.Controls.Add(create);
        var validate = Button("✓ Validate pack manifest…", async (_, _) => await ValidatePackAsync()); validate.Width = 230; panel.Controls.Add(validate);
        var open = Button("▣ Open last pack folder", (_, _) => OpenLastPack()); open.Width = 210; panel.Controls.Add(open);
        panel.Controls.Add(new Label { Text = "Select clips in Library first; duplicate WAS sources are stored only once.", AutoSize = true, Padding = new Padding(0, 14, 0, 0) });
        packPage.Controls.Add(packInfo); packPage.Controls.Add(panel);
    }
    private async Task CreatePackAsync()
    {
        if (busy) return;
        var selected = grid.SelectedRows.Cast<DataGridViewRow>().Select(r => r.DataBoundItem as Clip).Where(c => c is not null).Cast<Clip>().Where(c => c.Status == "Supported").DistinctBy(c => c.Identity).ToList();
        if (selected.Count == 0) { MessageBox.Show(this, "Select at least one supported clip in Library first.", "Create animation pack"); return; }
        string name = packName.Text.Trim();
        foreach (char invalid in Path.GetInvalidFileNameChars()) name = name.Replace(invalid, '_');
        name = name.Trim().TrimEnd('.');
        if (name.Length == 0) { MessageBox.Show(this, "Enter a valid pack name.", "Create animation pack"); return; }
        using var dialog = new FolderBrowserDialog { Description = "Choose where to create the animation pack", UseDescriptionForTitle = true };
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token => {
            string root = Path.Combine(dialog.SelectedPath, name + "-" + DateTime.Now.ToString("yyyyMMdd-HHmmss"));
            string sourceFolder = Path.Combine(root, "sources"); Directory.CreateDirectory(sourceFolder);
            var files = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase); var entries = new List<object>();
            foreach (Clip clip in selected) {
                token.ThrowIfCancellationRequested();
                string source = Path.GetFullPath(clip.Source);
                if (!File.Exists(source)) throw new FileNotFoundException("A selected WAS source is missing.", source);
                byte[] bytes = await File.ReadAllBytesAsync(source, token); string hash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
                string relative = "sources/" + hash + ".was"; string destination = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
                if (!files.ContainsKey(hash)) { await File.WriteAllBytesAsync(destination, bytes, token); files[hash] = relative; }
                entries.Add(new { id = clip.Name + "_0x" + clip.Offset.ToString("X"), displayName = clip.DisplayName, sourceSet = clip.Set, source = files[hash], recordOffset = clip.Offset, frames = clip.Frames, fps = clip.Fps, seconds = clip.Duration, status = clip.Status });
            }
            var manifest = new { format = "rdr1-animation-pack-v1", game = "Red Dead Redemption", name, character = packCharacter.SelectedItem?.ToString() ?? "John · bundled human rig", createdUtc = DateTime.UtcNow, entryCount = entries.Count, sourceCount = files.Count, entries, loader = new { status = "staged-only", note = "Requires a ScriptHookRDR runtime animation menu and a validated WAS installation path." } };
            await File.WriteAllTextAsync(Path.Combine(root, "manifest.json"), JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true }), token);
            await File.WriteAllTextAsync(Path.Combine(root, "README.txt"), "RDR1 Animation Studio custom animation pack\r\n\r\nThis pack contains deduplicated WAS sources and manifest metadata. It is staged for a runtime loader; it does not modify the game archive automatically.\r\nBack up animationres.rpf before any manual installation.\r\n", token);
            lastPackFolder = root; status.Text = $"Pack created · {entries.Count:N0} clips · {files.Count:N0} WAS sources";
            MessageBox.Show(this, "Pack created:\n\n" + root + "\n\nIt contains manifest.json, README.txt and a deduplicated sources folder.", "Animation pack complete");
        });
    }
    private async Task ValidatePackAsync()
    {
        using var dialog = new OpenFileDialog { Title = "Select an animation pack manifest", Filter = "Pack manifest|manifest.json|JSON|*.json" };
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        try {
            using var document = JsonDocument.Parse(await File.ReadAllTextAsync(dialog.FileName)); var root = document.RootElement;
            if (!root.TryGetProperty("format", out var format) || format.GetString() != "rdr1-animation-pack-v1") throw new InvalidDataException("Unknown pack format.");
            if (!root.TryGetProperty("entries", out var entries) || entries.ValueKind != JsonValueKind.Array) throw new InvalidDataException("The pack has no entries array.");
            int missing = 0, total = 0; string folder = Path.GetDirectoryName(dialog.FileName)!;
            foreach (var entry in entries.EnumerateArray()) {
                total++; string relative = entry.GetProperty("source").GetString() ?? ""; string combined = Path.GetFullPath(Path.Combine(folder, relative.Replace('/', Path.DirectorySeparatorChar)));
                if (Path.IsPathRooted(relative) || relative.Contains("..", StringComparison.Ordinal) || !combined.StartsWith(folder + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) || !File.Exists(combined)) missing++;
            }
            MessageBox.Show(this, missing == 0 ? $"Pack is valid.\n\n{total:N0} animation entries have available WAS sources." : $"Pack validation failed.\n\nMissing or unsafe sources: {missing:N0} of {total:N0}.", "Pack validation", MessageBoxButtons.OK, missing == 0 ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
        } catch (Exception ex) { MessageBox.Show(this, "Invalid pack manifest:\n" + ex.Message, "Pack validation", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
    private void OpenLastPack()
    {
        if (lastPackFolder is null || !Directory.Exists(lastPackFolder)) { MessageBox.Show(this, "Create a pack first.", "Animation packs"); return; }
        Process.Start(new ProcessStartInfo("explorer.exe", lastPackFolder) { UseShellExecute = true });
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
            var game = await GameLibrary.ScanAsync(dialog.SelectedPath, names, progress, token); gameLibrary = game; var input = new List<SourceFile>();
            string cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "Library"); Directory.CreateDirectory(cache);
            var errors = new List<string>(game.Errors);
            string collectionCache = CollectionCachePath(dialog.SelectedPath);
            CollectionCache? savedCollection = TryLoadCollectionCache(collectionCache);
            if (savedCollection is not null && CollectionCacheMatches(savedCollection, dialog.SelectedPath, game))
            {
                input.AddRange(savedCollection.Sources);
                progress.Report($"Using cached animation extraction… {input.Count:N0} sets");
            }
            else
            {
                await Task.Run(() => { int completed = 0; foreach (var group in game.AnimationSets.GroupBy(a => a.ArchivePath)) {
                    token.ThrowIfCancellationRequested(); using var archive = Rpf6Archive.Open(group.Key, names);
                    foreach (var asset in group) { token.ThrowIfCancellationRequested(); try {
                        byte[] bytes = archive.Extract(archive.Entries[asset.EntryIndex]); string file = Path.Combine(cache, Convert.ToHexString(SHA256.HashData(bytes)) + ".was");
                        if (!File.Exists(file)) File.WriteAllBytes(file, bytes);
                        input.Add(new(file, Path.GetRelativePath(dialog.SelectedPath, group.Key) + "/" + asset.InternalPath));
                    } catch (Exception ex) { errors.Add(asset.InternalPath + ": " + ex.Message); }
                    completed++; if (completed == 1 || completed % 25 == 0 || completed == game.AnimationSets.Count)
                        progress.Report($"Collecting animation sets… {completed:N0}/{game.AnimationSets.Count:N0}"); }
                } }, token);
                SaveCollectionCache(collectionCache, dialog.SelectedPath, game, input);
            }
            await IndexAsync(input, token); problems.AddRange(errors);
            SetCharacters(game.Characters);
            RefreshSceneResources();
            status.Text = $"Collection complete · {input.Count:N0} animation sets · mission inventory: {game.MissionScripts.Count:N0} scripts, {game.Cameras.Count:N0} cameras, {game.Props.Count:N0} props, {game.AiData.Count:N0} AI, {game.Audio.Count:N0} audio.";
        });
    }
    private async Task AddWasAsync() {
        if (busy) return; using var d = new OpenFileDialog { Title = "Add animation sets", Filter = "WAS|*.was", Multiselect = true }; if (d.ShowDialog(this) != DialogResult.OK) return;
        await Operation(async token => await IndexAsync(sources.Concat(d.FileNames.Select(f => new SourceFile(f, Path.GetFileName(f)))).Distinct().ToList(), token));
    }

    private static string CollectionCachePath(string gameDirectory)
    {
        string key = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(Path.GetFullPath(gameDirectory))))[..24];
        string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "Cache");
        Directory.CreateDirectory(root);
        return Path.Combine(root, "collection-" + key + ".json");
    }

    private static CollectionCache? TryLoadCollectionCache(string path)
    {
        try { return File.Exists(path) ? JsonSerializer.Deserialize<CollectionCache>(File.ReadAllText(path)) : null; }
        catch { return null; }
    }

    private static bool CollectionCacheMatches(CollectionCache cache, string root, GameLibrary game)
    {
        if (!string.Equals(cache.Root, Path.GetFullPath(root), StringComparison.OrdinalIgnoreCase) ||
            cache.AnimationSetCount != game.AnimationSets.Count || cache.Archives.Count != game.Archives.Count ||
            cache.Sources.Count != game.AnimationSets.Count) return false;
        foreach (ArchiveStamp stamp in cache.Archives)
        {
            try
            {
                FileInfo info = new(stamp.Path);
                if (info.Length != stamp.Length || info.LastWriteTimeUtc.Ticks != stamp.LastWriteUtcTicks) return false;
            }
            catch { return false; }
        }
        return cache.Sources.All(source => File.Exists(source.File));
    }

    private static void SaveCollectionCache(string path, string root, GameLibrary game, List<SourceFile> sources)
    {
        try
        {
            var stamps = game.Archives.Select(archive => {
                FileInfo info = new(archive);
                return new ArchiveStamp(archive, info.Length, info.LastWriteTimeUtc.Ticks);
            }).ToList();
            string temp = path + ".tmp";
            File.WriteAllText(temp, JsonSerializer.Serialize(new CollectionCache(Path.GetFullPath(root), game.AnimationSets.Count, stamps, sources)));
            File.Move(temp, path, true);
        }
        catch { /* A cache must never make collection fail. */ }
    }

    private static string CatalogCachePath(List<SourceFile> input)
    {
        var identity = input.Select(source => {
            try
            {
                FileInfo info = new(source.File);
                return new { source.File, source.Label, info.Length, LastWriteUtcTicks = info.LastWriteTimeUtc.Ticks };
            }
            catch { return new { source.File, source.Label, Length = -1L, LastWriteUtcTicks = -1L }; }
        }).ToList();
        string payload = "v0.10-catalog-2\n" + JsonSerializer.Serialize(identity);
        string key = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(payload)))[..32];
        string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "Cache", "Catalogs");
        Directory.CreateDirectory(root);
        return Path.Combine(root, key + ".json");
    }

    private static Catalog? TryLoadCatalogCache(string path)
    {
        try { return File.Exists(path) ? JsonSerializer.Deserialize<Catalog>(File.ReadAllText(path)) : null; }
        catch { return null; }
    }

    private static void SaveCatalogCache(string path, Catalog catalog)
    {
        try
        {
            string temp = path + ".tmp";
            File.WriteAllText(temp, JsonSerializer.Serialize(catalog));
            File.Move(temp, path, true);
        }
        catch { /* A cache must never make indexing fail. */ }
    }

    private async Task IndexAsync(List<SourceFile> input, CancellationToken token) {
        string job = Job(), manifest = Path.Combine(job, "sources.json"), output = Path.Combine(job, "catalog.json");
        await File.WriteAllTextAsync(manifest, JsonSerializer.Serialize(input), token);
        string cachedCatalog = CatalogCachePath(input);
        Catalog? result = TryLoadCatalogCache(cachedCatalog);
        if (result is null)
        {
            await Python("studio_library.py", ["index", manifest, output], token);
            result = JsonSerializer.Deserialize<Catalog>(await File.ReadAllTextAsync(output, token)) ?? throw new InvalidDataException("Empty catalog");
            SaveCatalogCache(cachedCatalog, result);
        }
        else status.Text = "Loaded cached animation catalog…";
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
            string reportPath = Path.Combine(output, "export-report.json");
            if (MessageBox.Show(this, "Saved Blender Actions and export report:\n" + output + "\n\nUnsupported clips are reported without substituting another animation.\n\nOpen the export report in the JSON viewer now?", "Export complete", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                ShowJsonReport(reportPath);
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
            // Bump the cache namespace after fixing armature export; existing v0.10
            // previews may contain mesh-only GLBs and must be regenerated.
            string key = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes("v010.1-character|" + characterKey + "|" + clip.Identity + "|" + clip.Fingerprint)));
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
            // WebView2 can take a while on its first launch, especially while it
            // initializes the per-user browser cache.  Do not report a healthy
            // generated model as broken after the old 30-second cutoff.
            await preview.Loaded.Task.WaitAsync(TimeSpan.FromMinutes(2), token);
            status.Text = "Source name: " + clip.Name + " · " + clip.Set + " · Preview: " + selectedCharacter.Label + " · Mouse drag: orbit, wheel: zoom";
        } catch (OperationCanceledException) { }
        catch (TimeoutException) { if (version == previewVersion && !IsDisposed) preview.Notice("Preview is still loading. Try selecting the clip again if the character does not appear."); }
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
    private Task Python(string script, string[] args, CancellationToken token) => Execute(FindWorkingPython() ?? throw new FileNotFoundException("Python 3.12 or Blender's embedded Python is required by the animation engine."), new[] { Path.Combine(ToolPath, script) }.Concat(args).ToArray(), token, true);
    private async Task Execute(string exe, string[] args, CancellationToken token, bool python = false) {
        var start = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true }; foreach (string arg in args) start.ArgumentList.Add(arg);
        if (python)
        {
            string dependencies = Path.Combine(ToolPath, "_deps");
            if (Directory.Exists(dependencies) && NeedsBundledPythonDependencies(exe))
                start.Environment["PYTHONPATH"] = dependencies;
            else
                // Do not let a Python 3.12-only bundled extension shadow a
                // compatible package shipped by Blender's embedded runtime.
                start.Environment.Remove("PYTHONPATH");
        }
        using var p = Process.Start(start) ?? throw new InvalidOperationException("Could not start animation engine");
        using var registration = token.Register(() => { try { p.Kill(true); } catch { } });
        var errors = p.StandardError.ReadToEndAsync(); var output = new System.Text.StringBuilder();
        while (await p.StandardOutput.ReadLineAsync(token) is { } line) { output.AppendLine(line); status.Text = line.Length > 180 ? line[..180] : line; }
        await p.WaitForExitAsync(token); string error = await errors;
        if (p.ExitCode != 0) throw new InvalidOperationException(SummarizeProcessFailure(error, output.ToString()));
    }
    private static bool NeedsBundledPythonDependencies(string executable)
    {
        lock (pythonProbeLock)
        {
            if (pythonDependencyFallback.TryGetValue(executable, out bool cached)) return cached;
            bool needs = true;
            try
            {
                // Probe the interpreter without our optional dependency path.
                // Blender 5.2 includes a Python 3.13-compatible zstandard;
                // older/system Python runtimes use the bundled 3.12 wheel.
                var probe = new ProcessStartInfo(executable)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                probe.ArgumentList.Add("-c");
                probe.ArgumentList.Add("import zstandard");
                probe.Environment.Remove("PYTHONPATH");
                using var process = Process.Start(probe);
                needs = process is null || !process.WaitForExit(3000) || process.ExitCode != 0;
                if (process is not null && !process.HasExited) process.Kill(true);
            }
            catch { needs = true; }
            pythonDependencyFallback[executable] = needs;
            return needs;
        }
    }
    private static string SummarizeProcessFailure(string error, string output)
    {
        string combined = (error + "\n" + output).Trim();
        if (combined.Length == 0) return "The external tool failed without an error message.";
        string[] lines = combined.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        string? detail = lines.LastOrDefault(line => line.Contains("Error:", StringComparison.OrdinalIgnoreCase) || line.Contains("Exception:", StringComparison.OrdinalIgnoreCase));
        if (detail is not null) return detail;
        return string.Join(Environment.NewLine, lines.TakeLast(Math.Min(4, lines.Length)));
    }
    internal static string? FindWorkingPython() {
        lock (pythonProbeLock)
        {
            if (pythonProbeComplete) return pythonPath;
            var candidates = new List<string>();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            void Add(string? candidate)
            {
                if (string.IsNullOrWhiteSpace(candidate)) return;
                try { candidate = Path.GetFullPath(candidate); } catch { return; }
                if (File.Exists(candidate) && seen.Add(candidate)) candidates.Add(candidate);
            }
            // PATH is cheap and covers normal Python installations.  The old
            // implementation recursively walked the entire Codex runtime tree
            // first, which could make an 8 KB mission take a minute to start.
            string? pathEnvironment = Environment.GetEnvironmentVariable("PATH");
            if (pathEnvironment is not null) foreach (string directory in pathEnvironment.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries)) Add(Path.Combine(directory.Trim(), "python.exe"));
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string user = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            foreach (string version in new[] { "Python312", "Python313", "Python311" })
            {
                Add(Path.Combine(local, "Programs", "Python", version, "python.exe"));
                Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), version, "python.exe"));
                Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Python", version, "python.exe"));
            }
            // Blender ships a self-contained Python runtime.  It is a useful
            // zero-install fallback for the bundled inspectors (including
            // zstandard on current Blender releases), so discover it without
            // recursively walking the whole Program Files tree.
            string blenderRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Blender Foundation");
            if (Directory.Exists(blenderRoot))
            {
                try
                {
                    foreach (string versionDirectory in Directory.EnumerateDirectories(blenderRoot, "Blender *", SearchOption.TopDirectoryOnly))
                    {
                        Add(Path.Combine(versionDirectory, "5.2", "python", "bin", "python.exe"));
                        foreach (string runtimeDirectory in Directory.EnumerateDirectories(versionDirectory, "*", SearchOption.TopDirectoryOnly))
                            Add(Path.Combine(runtimeDirectory, "python", "bin", "python.exe"));
                    }
                }
                catch { }
            }
            // Only if the quick probes fail, use a bounded-depth fallback for
            // the bundled runtime.  Enumerating the complete tree is avoided.
            foreach (string root in new[] { Path.Combine(user, ".cache", "codex-runtimes"), Path.Combine(local, "Programs", "Python") })
            {
                if (!Directory.Exists(root)) continue;
                try
                {
                    foreach (string directory in Directory.EnumerateDirectories(root, "*", SearchOption.TopDirectoryOnly))
                    {
                        Add(Path.Combine(directory, "python.exe"));
                        foreach (string child in Directory.EnumerateDirectories(directory, "*", SearchOption.TopDirectoryOnly)) Add(Path.Combine(child, "python.exe"));
                    }
                }
                catch { }
            }
            foreach (string candidate in candidates)
            {
                try
                {
                    using var p = Process.Start(new ProcessStartInfo(candidate, "--version") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true });
                    if (p is not null && p.WaitForExit(1500))
                    {
                        string version = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
                        if (version.Contains("Python 3.12", StringComparison.OrdinalIgnoreCase) ||
                            version.Contains("Python 3.13", StringComparison.OrdinalIgnoreCase)) { pythonPath = candidate; break; }
                    }
                }
                catch { }
            }
            pythonProbeComplete = true;
            return pythonPath;
        }
    }
    internal static string? FindNativeDatabase() {
        string? configured = Environment.GetEnvironmentVariable("RDR1_NATIVE_DB");
        if (!string.IsNullOrWhiteSpace(configured) && File.Exists(configured)) return Path.GetFullPath(configured);
        string bundled = Path.Combine(AppContext.BaseDirectory, "Assets", "rdr1-natives.json");
        if (File.Exists(bundled)) return bundled;
        string local = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "natives.json");
        return File.Exists(local) ? local : null;
    }
}
