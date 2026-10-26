using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using System.Text.Json;

namespace RDR1AnimationStudio;

internal sealed class AnimationPreview : UserControl
{
    private readonly WebView2 browser = new() { Dock = DockStyle.Fill };
    private readonly Label message = new() { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, Text = "Blender-first preview.\nSelect an animation, then use Open in Blender." };
    private Task? initialization;
    private string? pendingModel;
    private int navigationGeneration;
    internal TaskCompletionSource<string> Loaded { get; private set; } = new();
    internal AnimationPreview() { Controls.Add(browser); Controls.Add(message); }
    internal void Notice(string text) { message.Text = text; message.Visible = true; browser.Visible = false; }
    internal void KeepBrowserVisibleNotice(string text) { message.Text = text; message.Visible = false; browser.Visible = true; }
    private async Task InitializeAsync()
    {
        var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio", "WebView2"));
        await browser.EnsureCoreWebView2Async(env);
        // The viewer loads the generated GLB from model.local directly. Allow
        // the two local virtual hosts to communicate; this avoids depending on
        // a WebView2 postMessage handshake for the normal preview path.
        browser.CoreWebView2.SetVirtualHostNameToFolderMapping("appassets.local", Path.Combine(AppContext.BaseDirectory, "Renderer"), CoreWebView2HostResourceAccessKind.Allow);
        browser.CoreWebView2.WebMessageReceived += (_, e) => {
            try {
                using var doc = JsonDocument.Parse(e.TryGetWebMessageAsString());
                var root = doc.RootElement;
                string? type = root.TryGetProperty("type", out var typeElement) ? typeElement.GetString() : null;
                if (type == "request-model" && pendingModel is not null) _ = SendModelAsync(pendingModel);
                else if (type == "ready") { message.Visible = false; Loaded.TrySetResult(root.ToString()); }
                else if (type == "debug") {
                    string detail = root.TryGetProperty("message", out var debugElement) ? debugElement.GetString() ?? "" : "";
                    try {
                        string logDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RDR1AnimationStudio");
                        Directory.CreateDirectory(logDir);
                        File.AppendAllText(Path.Combine(logDir, "preview-debug.log"), $"{DateTime.Now:O} {detail}\n");
                    } catch { }
                }
                else if (type == "error") {
                    string detail = root.TryGetProperty("message", out var messageElement) ? messageElement.GetString() ?? "Unknown viewer error" : "Unknown viewer error";
                    Notice("Preview failed: " + detail); Loaded.TrySetException(new InvalidOperationException(detail));
                }
            } catch (Exception ex) {
                Notice("Preview message failed: " + ex.Message);
                Loaded.TrySetException(ex);
            }
        };
        browser.CoreWebView2.ProcessFailed += (_, e) => Notice("Preview process stopped: " + e.ProcessFailedKind);
        browser.CoreWebView2.NavigationCompleted += (_, e) => {
            if (!e.IsSuccess) { Notice("Preview could not load: " + e.WebErrorStatus); return; }
            // Some WebView2 builds occasionally lose the first web-message when
            // the module script and virtual-host mapping initialize together.
            // Send once after navigation as a fallback; the viewer de-duplicates
            // model-data messages and still supports the normal request handshake.
            string? model = pendingModel;
            int generation = navigationGeneration;
            if (model is not null) _ = SendModelAfterNavigationAsync(model, generation);
        };
        browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
    }
    internal async Task LoadModelAsync(string file, string name)
    {
        initialization ??= InitializeAsync(); await initialization;
        pendingModel = file;
        navigationGeneration++;
        Loaded = new(); browser.Visible = true;
        browser.CoreWebView2.SetVirtualHostNameToFolderMapping("model.local", Path.GetDirectoryName(file)!, CoreWebView2HostResourceAccessKind.Allow);
        browser.Source = new Uri("https://appassets.local/viewer.html?model=" + Uri.EscapeDataString(Path.GetFileName(file)) + "&title=" + Uri.EscapeDataString(name) + "&nonce=" + Guid.NewGuid().ToString("N"));
    }
    private async Task SendModelAfterNavigationAsync(string file, int generation)
    {
        try {
            await Task.Delay(150);
            if (generation == navigationGeneration && !IsDisposed) await SendModelAsync(file);
        } catch (Exception ex) {
            if (generation == navigationGeneration && !IsDisposed) {
                Notice("Preview model transfer failed: " + ex.Message);
                Loaded.TrySetException(ex);
            }
        }
    }
    private async Task SendModelAsync(string file)
    {
        try {
            byte[] bytes = await File.ReadAllBytesAsync(file);
            string payload = Convert.ToBase64String(bytes);
            browser.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new { type = "model-data", data = payload }));
        } catch (Exception ex) {
            browser.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new { type = "error", message = ex.Message }));
        }
    }
    internal async Task CaptureAsync(string path)
    {
        using var stream = File.Create(path);
        await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
    }
}
