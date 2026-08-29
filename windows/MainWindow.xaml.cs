using Microsoft.Win32;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;
using Forms = System.Windows.Forms;

namespace SingBoxSmart.Windows;

public partial class MainWindow : Window
{
    private const string ProxyAddress = "127.0.0.1:20808";
    private static readonly string LegacyDataDirectory = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "sing-box-smart");
    private readonly string _dataDirectory = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "smart-box");
    private readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromSeconds(45) };
    private readonly DispatcherTimer _refreshTimer = new() { Interval = TimeSpan.FromMinutes(30) };
    private readonly Forms.NotifyIcon _trayIcon;
    private Process? _coreProcess;
    private ClientSettings _settings = new();
    private bool _privatePathVisible;
    private bool _allowExit;

    private string SettingsPath => Path.Combine(_dataDirectory, "settings.json");
    private string ProfilePath => Path.Combine(_dataDirectory, "profile.json");
    private string CorePath => Path.Combine(AppContext.BaseDirectory, "smart-box-core.exe");

    public MainWindow()
    {
        InitializeComponent();
        MigrateLegacyData();
        Directory.CreateDirectory(_dataDirectory);
        DataPathText.Text = _dataDirectory;
        LoadSettings();
        PopulateEndpointFields(_settings.SubscriptionUrl);
        if (IsSystemProxyEnabled()) SetSystemProxy(false);
        SystemProxyCheckBox.IsChecked = false;
        UpdatePathMask();
        UpdateProfileStatus();
        SetStoppedState();

        _refreshTimer.Tick += async (_, _) => await PullProfileAsync(startAfterPull: false);
        _refreshTimer.Start();

        var trayMenu = new Forms.ContextMenuStrip();
        trayMenu.Items.Add("显示", null, (_, _) => ShowFromTray());
        trayMenu.Items.Add("启动 / 停止", null, async (_, _) => await ToggleCoreAsync());
        trayMenu.Items.Add(new Forms.ToolStripSeparator());
        trayMenu.Items.Add("退出", null, (_, _) => ExitApplication());
        _trayIcon = new Forms.NotifyIcon
        {
            Text = "smart-box",
            Icon = System.Drawing.SystemIcons.Shield,
            Visible = true,
            ContextMenuStrip = trayMenu,
        };
        _trayIcon.DoubleClick += (_, _) => ShowFromTray();

        Closing += (_, args) =>
        {
            if (!_allowExit)
            {
                args.Cancel = true;
                Hide();
                _trayIcon.ShowBalloonTip(1200, "smart-box", "客户端仍在后台运行", Forms.ToolTipIcon.Info);
            }
        };

        AppendLog("客户端已就绪。请填写树莓派总订阅地址。");
    }

    private async void PullButton_Click(object sender, RoutedEventArgs e)
    {
        await PullProfileAsync(startAfterPull: _coreProcess is not null);
    }

    private async Task PullProfileAsync(bool startAfterPull)
    {
        if (!TryBuildSubscriptionUri(out var uri, out var validationError))
        {
            AppendLog(validationError, isError: true);
            return;
        }

        SetBusy(true);
        var tempPath = ProfilePath + ".new";
        try
        {
            AppendLog("正在拉取总订阅...");
            using var request = new HttpRequestMessage(HttpMethod.Get, uri);
            request.Headers.UserAgent.ParseAdd("smart-box-windows/0.1.0");
            using var response = await _httpClient.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var content = await response.Content.ReadAsStringAsync();
            await File.WriteAllTextAsync(tempPath, content);

            await ValidateProfileAsync(tempPath);
            File.Move(tempPath, ProfilePath, true);
            _settings.SubscriptionUrl = uri.AbsoluteUri;
            _settings.LastPullUtc = DateTimeOffset.UtcNow;
            SaveSettings();
            UpdateProfileStatus();
            AppendLog("总订阅已通过核心校验并保存。");

            if (startAfterPull)
            {
                await StopCoreAsync();
                await StartCoreAsync();
            }
        }
        catch (Exception exception)
        {
            AppendLog($"拉取失败：{exception.Message}", isError: true);
        }
        finally
        {
            if (File.Exists(tempPath)) File.Delete(tempPath);
            SetBusy(false);
        }
    }

    private async Task ValidateProfileAsync(string path)
    {
        EnsureCoreExists();
        using var process = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = CorePath,
                Arguments = $"check -c \"{path}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                WorkingDirectory = _dataDirectory,
            },
        };
        process.Start();
        var outputTask = process.StandardOutput.ReadToEndAsync();
        var errorTask = process.StandardError.ReadToEndAsync();
        await process.WaitForExitAsync();
        var output = (await outputTask) + (await errorTask);
        if (process.ExitCode != 0)
        {
            throw new InvalidDataException(output.Trim());
        }
    }

    private async void StartStopButton_Click(object sender, RoutedEventArgs e) => await ToggleCoreAsync();

    private async Task ToggleCoreAsync()
    {
        if (_coreProcess is null)
        {
            await StartCoreAsync();
        }
        else
        {
            await StopCoreAsync();
        }
    }

    private async Task StartCoreAsync()
    {
        try
        {
            EnsureCoreExists();
            if (!File.Exists(ProfilePath)) throw new FileNotFoundException("请先保存并拉取总订阅");

            _coreProcess = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = CorePath,
                    Arguments = $"run -c \"{ProfilePath}\"",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    WorkingDirectory = _dataDirectory,
                },
                EnableRaisingEvents = true,
            };
            _coreProcess.OutputDataReceived += (_, args) => { if (args.Data is not null) Dispatcher.Invoke(() => AppendLog(args.Data)); };
            _coreProcess.ErrorDataReceived += (_, args) => { if (args.Data is not null) Dispatcher.Invoke(() => AppendLog(args.Data, args.Data.Contains("ERROR"))); };
            _coreProcess.Exited += (_, _) => Dispatcher.Invoke(() =>
            {
                _coreProcess?.Dispose();
                _coreProcess = null;
                SetStoppedState();
                DisableSystemProxyIfNeeded();
                AppendLog("代理核心已停止。");
            });
            _coreProcess.Start();
            _coreProcess.BeginOutputReadLine();
            _coreProcess.BeginErrorReadLine();
            SetRunningState();
            AppendLog("代理核心已启动。");
            await Task.CompletedTask;
        }
        catch (Exception exception)
        {
            _coreProcess?.Dispose();
            _coreProcess = null;
            SetStoppedState();
            AppendLog($"启动失败：{exception.Message}", isError: true);
        }
    }

    private async Task StopCoreAsync()
    {
        var process = _coreProcess;
        if (process is null) return;
        _coreProcess = null;
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync();
            }
        }
        catch (InvalidOperationException)
        {
        }
        finally
        {
            process.Dispose();
            SetStoppedState();
        }
    }

    private void SystemProxyCheckBox_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            SetSystemProxy(SystemProxyCheckBox.IsChecked == true);
            AppendLog(SystemProxyCheckBox.IsChecked == true ? "Windows 系统代理已启用。" : "Windows 系统代理已关闭。");
        }
        catch (Exception exception)
        {
            AppendLog($"系统代理切换失败：{exception.Message}", isError: true);
        }
    }

    private static void SetSystemProxy(bool enabled)
    {
        using var key = Registry.CurrentUser.OpenSubKey("Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings", true)
            ?? throw new InvalidOperationException("无法打开系统代理设置");
        key.SetValue("ProxyEnable", enabled ? 1 : 0, RegistryValueKind.DWord);
        key.SetValue("ProxyServer", ProxyAddress, RegistryValueKind.String);
        InternetSetOption(IntPtr.Zero, 39, IntPtr.Zero, 0);
        InternetSetOption(IntPtr.Zero, 37, IntPtr.Zero, 0);
    }

    private static bool IsSystemProxyEnabled()
    {
        using var key = Registry.CurrentUser.OpenSubKey("Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings");
        return Convert.ToInt32(key?.GetValue("ProxyEnable", 0)) == 1 &&
            string.Equals(key?.GetValue("ProxyServer")?.ToString(), ProxyAddress, StringComparison.OrdinalIgnoreCase);
    }

    private void RevealButton_Click(object sender, RoutedEventArgs e)
    {
        _privatePathVisible = !_privatePathVisible;
        RevealButton.Content = _privatePathVisible ? "\uE891" : "\uE890";
        UpdatePathMask();
    }

    private void PrivatePathBox_TextChanged(object sender, System.Windows.Controls.TextChangedEventArgs e) => UpdatePathMask();

    private void UpdatePathMask()
    {
        if (MaskedPath is null || PrivatePathBox is null) return;
        MaskedPath.Visibility = _privatePathVisible || string.IsNullOrEmpty(PrivatePathBox.Text)
            ? Visibility.Collapsed
            : Visibility.Visible;
    }

    private void ProtocolBox_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (PortBox is null) return;
        var scheme = GetSelectedScheme();
        if (scheme == Uri.UriSchemeHttps && PortBox.Text == "80") PortBox.Text = "443";
        if (scheme == Uri.UriSchemeHttp && PortBox.Text == "443") PortBox.Text = "80";
    }

    private void PortBox_PreviewTextInput(object sender, System.Windows.Input.TextCompositionEventArgs e)
    {
        e.Handled = e.Text.Any(character => !char.IsDigit(character));
    }

    private void SetBusy(bool busy)
    {
        PullButton.IsEnabled = !busy;
        ProtocolBox.IsEnabled = !busy;
        HostBox.IsEnabled = !busy;
        PortBox.IsEnabled = !busy;
        PrivatePathBox.IsEnabled = !busy;
        RevealButton.IsEnabled = !busy;
        PullButton.Content = busy ? "正在拉取..." : PullButton.Content;
        if (!busy)
        {
            PullButton.Content = new System.Windows.Controls.StackPanel
            {
                Orientation = System.Windows.Controls.Orientation.Horizontal,
                Children =
                {
                    new System.Windows.Controls.TextBlock { Text = "\uE896", FontFamily = new System.Windows.Media.FontFamily("Segoe Fluent Icons"), Foreground = System.Windows.Media.Brushes.White, FontSize = 15, Margin = new Thickness(0, 0, 8, 0) },
                    new System.Windows.Controls.TextBlock { Text = "保存并拉取", Foreground = System.Windows.Media.Brushes.White },
                },
            };
        }
    }

    private void SetRunningState()
    {
        StatusText.Text = "运行中";
        StatusDot.Fill = new SolidColorBrush(System.Windows.Media.Color.FromRgb(70, 205, 162));
        StatusBadge.Background = new SolidColorBrush(System.Windows.Media.Color.FromRgb(26, 91, 76));
        StartStopIcon.Text = "\uE71A";
        StartStopLabel.Text = "停止";
        SystemProxyCheckBox.IsEnabled = true;
    }

    private void SetStoppedState()
    {
        StatusText.Text = "未运行";
        StatusDot.Fill = new SolidColorBrush(System.Windows.Media.Color.FromRgb(135, 147, 143));
        StatusBadge.Background = new SolidColorBrush(System.Windows.Media.Color.FromRgb(52, 65, 62));
        StartStopIcon.Text = "\uE768";
        StartStopLabel.Text = "启动";
        SystemProxyCheckBox.IsEnabled = false;
    }

    private void UpdateProfileStatus()
    {
        ProfileStatusText.Text = File.Exists(ProfilePath) && _settings.LastPullUtc is not null
            ? $"上次拉取 {_settings.LastPullUtc.Value.ToLocalTime():yyyy-MM-dd HH:mm}"
            : "尚未拉取配置";
    }

    private void AppendLog(string message, bool isError = false)
    {
        var prefix = isError ? "ERROR" : "INFO ";
        LogBox.AppendText($"{DateTime.Now:HH:mm:ss}  {prefix}  {message}{Environment.NewLine}");
        LogBox.ScrollToEnd();
    }

    private void ClearLogButton_Click(object sender, RoutedEventArgs e) => LogBox.Clear();

    private void LoadSettings()
    {
        try
        {
            if (File.Exists(SettingsPath))
            {
                _settings = JsonSerializer.Deserialize<ClientSettings>(File.ReadAllText(SettingsPath)) ?? new ClientSettings();
            }
        }
        catch
        {
            _settings = new ClientSettings();
        }
    }

    private void PopulateEndpointFields(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) ||
            (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
        {
            ProtocolBox.SelectedIndex = 0;
            HostBox.Text = string.Empty;
            PortBox.Text = "80";
            PrivatePathBox.Text = string.Empty;
            return;
        }

        ProtocolBox.SelectedIndex = uri.Scheme == Uri.UriSchemeHttps ? 1 : 0;
        HostBox.Text = uri.Host;
        PortBox.Text = (uri.IsDefaultPort ? (uri.Scheme == Uri.UriSchemeHttps ? 443 : 80) : uri.Port).ToString();
        var path = "/" + uri.GetComponents(UriComponents.Path, UriFormat.UriEscaped).TrimStart('/');
        var query = uri.GetComponents(UriComponents.Query, UriFormat.UriEscaped);
        PrivatePathBox.Text = string.IsNullOrEmpty(query) ? path : $"{path}?{query}";
    }

    private bool TryBuildSubscriptionUri(out Uri uri, out string error)
    {
        uri = null!;
        error = string.Empty;
        var scheme = GetSelectedScheme();
        var host = HostBox.Text.Trim().TrimStart('[').TrimEnd(']');
        if (host.Length == 0 || host.Any(character => char.IsWhiteSpace(character) || character == '/') ||
            Uri.CheckHostName(host) == UriHostNameType.Unknown)
        {
            error = "请填写有效的域名或 IP。";
            return false;
        }
        if (!int.TryParse(PortBox.Text, out var port) || port is < 1 or > 65535)
        {
            error = "端口必须在 1 到 65535 之间。";
            return false;
        }

        var privatePath = PrivatePathBox.Text.Trim();
        if (privatePath.Length == 0)
        {
            error = "请填写私密订阅路径。";
            return false;
        }
        if (!privatePath.StartsWith('/')) privatePath = "/" + privatePath;
        var separator = privatePath.IndexOf('?');
        var path = separator >= 0 ? privatePath[..separator] : privatePath;
        var query = separator >= 0 ? privatePath[(separator + 1)..] : string.Empty;

        try
        {
            uri = new UriBuilder(scheme, host, port, path) { Query = query }.Uri;
            return true;
        }
        catch (UriFormatException)
        {
            error = "订阅地址格式无效。";
            return false;
        }
    }

    private string GetSelectedScheme()
    {
        return (ProtocolBox.SelectedItem as System.Windows.Controls.ComboBoxItem)?.Tag?.ToString() == Uri.UriSchemeHttps
            ? Uri.UriSchemeHttps
            : Uri.UriSchemeHttp;
    }

    private void MigrateLegacyData()
    {
        if (!Directory.Exists(LegacyDataDirectory)) return;
        Directory.CreateDirectory(_dataDirectory);
        foreach (var fileName in new[] { "settings.json", "profile.json" })
        {
            var source = Path.Combine(LegacyDataDirectory, fileName);
            var destination = Path.Combine(_dataDirectory, fileName);
            if (File.Exists(source) && !File.Exists(destination)) File.Copy(source, destination);
        }
    }

    private void SaveSettings()
    {
        var json = JsonSerializer.Serialize(_settings, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(SettingsPath, json);
    }

    private void EnsureCoreExists()
    {
        if (!File.Exists(CorePath)) throw new FileNotFoundException("缺少 smart-box-core.exe", CorePath);
    }

    private void ShowFromTray()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
    }

    private void ExitApplication()
    {
        _allowExit = true;
        _refreshTimer.Stop();
        DisableSystemProxyIfNeeded();
        _trayIcon.Visible = false;
        _trayIcon.Dispose();
        _coreProcess?.Kill(entireProcessTree: true);
        Close();
    }

    private void DisableSystemProxyIfNeeded()
    {
        if (SystemProxyCheckBox.IsChecked != true && !IsSystemProxyEnabled()) return;
        SetSystemProxy(false);
        SystemProxyCheckBox.IsChecked = false;
    }

    [DllImport("wininet.dll", SetLastError = true)]
    private static extern bool InternetSetOption(IntPtr hInternet, int option, IntPtr buffer, int bufferLength);

    private sealed class ClientSettings
    {
        public string SubscriptionUrl { get; set; } = string.Empty;
        public DateTimeOffset? LastPullUtc { get; set; }
    }
}
