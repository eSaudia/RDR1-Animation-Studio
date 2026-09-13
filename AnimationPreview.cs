using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using System.Text.Json;

namespace RDR1AnimationStudio;

internal sealed class AnimationPreview : UserControl
{
    private readonly WebView2 browser = new() { Dock = DockStyle.Fill };
    private readonly Label message = new() { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, Text = "Select one supported animation to preview John.\nAnimal rigs are not supported yet." };
    private Task? initialization;
    internal TaskCompletionSource<string> Loaded { get; private set; } = new();
    internal AnimationPreview() { Controls.Add(browser); Controls.Add(message); }
    internal void Notice(string text) { message.Text = text; message.Visible = true; browser.Visible = false; }
    private async Task InitializeAsync()
    {
        var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "WebView2"));
        await browser.EnsureCoreWebView2Async(env);
        browser.CoreWebView2.SetVirtualHostNameToFolderMapping("appassets.local", Path.Combine(AppContext.BaseDirectory, "Renderer"), CoreWebView2HostResourceAccessKind.DenyCors);
        browser.CoreWebView2.WebMessageReceived += (_, e) => {
            using var doc = JsonDocument.Parse(e.TryGetWebMessageAsString());
            var root = doc.RootElement;
            if (root.GetProperty("type").GetString() == "ready") { message.Visible = false; Loaded.TrySetResult(root.ToString()); }
            else { Notice("Preview failed: " + root.GetProperty("message").GetString()); Loaded.TrySetException(new InvalidOperationException(message.Text)); }
        };
        browser.CoreWebView2.ProcessFailed += (_, e) => Notice("Preview process stopped: " + e.ProcessFailedKind);
        browser.CoreWebView2.NavigationCompleted += (_, e) => { if (!e.IsSuccess) Notice("Preview could not load: " + e.WebErrorStatus); };
        browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
    }
    internal async Task LoadModelAsync(string file, string name)
    {
        initialization ??= InitializeAsync(); await initialization;
        Loaded = new(); browser.Visible = true;
        browser.CoreWebView2.SetVirtualHostNameToFolderMapping("model.local", Path.GetDirectoryName(file)!, CoreWebView2HostResourceAccessKind.Allow);
        browser.Source = new Uri("https://appassets.local/viewer.html?model=" + Uri.EscapeDataString(Path.GetFileName(file)) + "&title=" + Uri.EscapeDataString(name) + "&nonce=" + Guid.NewGuid().ToString("N"));
    }
    internal async Task CaptureAsync(string path)
    {
        using var stream = File.Create(path);
        await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
    }
}
