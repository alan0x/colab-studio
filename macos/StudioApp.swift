import Cocoa
import WebKit

final class StudioAppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var serverProcess: Process?
    private var serverPort: Int = 8765
    private var pendingDownloadDestination: URL?

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMainMenu()
        setupWindow()
        startBackendAndLoad()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let proc = serverProcess, proc.isRunning {
            proc.terminate()
            proc.waitUntilExit()
        }
    }

    // MARK: - Native Menu Bar (enables Cmd+C / Cmd+V / Cmd+A / Cmd+Z / Cmd+Q in WKWebView)
    private func setupMainMenu() {
        let mainMenu = NSMenu()

        // App menu
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 Colab Canvas Studio", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏 Colab Canvas Studio", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "退出 Colab Canvas Studio", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu

        // Edit menu (critical for paste/copy inside prompt textarea & OAuth input)
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu

        // View menu
        let viewMenuItem = NSMenuItem()
        mainMenu.addItem(viewMenuItem)
        let viewMenu = NSMenu(title: "视图")
        viewMenu.addItem(withTitle: "重新加载画布", action: #selector(reloadCanvas), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "进入/退出全屏", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        viewMenuItem.submenu = viewMenu

        // Window menu
        let windowMenuItem = NSMenuItem()
        mainMenu.addItem(windowMenuItem)
        let windowMenu = NSMenu(title: "窗口")
        windowMenu.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowMenuItem.submenu = windowMenu

        NSApp.mainMenu = mainMenu
    }

    @objc private func reloadCanvas() {
        webView?.reload()
    }

    // MARK: - Window & WKWebView
    private func setupWindow() {
        let screenRect = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let w = min(1460, screenRect.width * 0.9)
        let h = min(920, screenRect.height * 0.9)
        let frame = NSRect(
            x: screenRect.midX - w / 2,
            y: screenRect.midY - h / 2,
            width: w,
            height: h
        )

        window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Colab Canvas Studio"
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = NSColor(red: 8/255, green: 9/255, blue: 14/255, alpha: 1.0)
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = false
        window.minSize = NSSize(width: 960, height: 640)

        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "allowFileAccessFromFileURLs")
        config.mediaTypesRequiringUserActionForPlayback = []

        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        // Instant sleek splash while local Python server boots (< 0.4s)
        let splashHTML = """
        <!doctype html><html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#08090e;color:#e2e8f0;font-family:-apple-system,sans-serif;">
          <div style="text-align:center;">
            <div style="width:52px;height:52px;margin:0 auto 14px;border-radius:16px;background:linear-gradient(135deg,#6366f1,#a855f7);display:grid;place-items:center;font-size:24px;">✦</div>
            <div style="font-size:17px;font-weight:700;margin-bottom:6px;">Colab Canvas Studio</div>
            <div style="font-size:12.5px;color:#8b95ad;">正在启动本地创作画布引擎…</div>
          </div>
        </body></html>
        """
        webView.loadHTMLString(splashHTML, baseURL: nil)

        window.contentView?.addSubview(webView)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: - Managed Python Backend
    private func pickFreePort(preferred: Int = 8765) -> Int {
        for candidate in preferred..<(preferred + 30) {
            let sock = socket(AF_INET, SOCK_STREAM, 0)
            if sock < 0 { continue }
            var addr = sockaddr_in()
            addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            addr.sin_family = sa_family_t(AF_INET)
            addr.sin_port = in_port_t(candidate).bigEndian
            addr.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
            let bindResult = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            close(sock)
            if bindResult == 0 {
                return candidate
            }
        }
        return preferred
    }

    private func findPythonExecutable() -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            "\(home)/miniconda3/bin/python3",
            "\(home)/anaconda3/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3"
        ]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        return "/usr/bin/python3"
    }

    private func startBackendAndLoad() {
        serverPort = pickFreePort(preferred: 8765)
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        guard let resourceDir = Bundle.main.resourcePath else { return }
        let serverScript = "\(resourceDir)/local_app/server.py"

        // Store outputs & config in user's persistent project directory if present, else Application Support
        let projectDir = "\(home)/Documents/projects/colab-media-studio"
        let dataDir = FileManager.default.fileExists(atPath: projectDir)
            ? projectDir
            : "\(home)/Library/Application Support/ColabCanvasStudio"
        try? FileManager.default.createDirectory(atPath: dataDir, withIntermediateDirectories: true)

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: findPythonExecutable())
        proc.arguments = [serverScript, "--no-browser"]

        var env = ProcessInfo.processInfo.environment
        let extraPath = [
            "\(home)/.local/bin",
            "\(home)/miniconda3/bin",
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin"
        ].joined(separator: ":")
        env["PATH"] = extraPath + ":" + (env["PATH"] ?? "")
        env["STUDIO_PORT"] = String(serverPort)
        env["STUDIO_DATA_DIR"] = dataDir
        env["PYTHONUNBUFFERED"] = "1"
        proc.environment = env

        let logPipe = Pipe()
        proc.standardOutput = logPipe
        proc.standardError = logPipe

        do {
            try proc.run()
            serverProcess = proc
        } catch {
            showStartupError("启动本地引擎失败：\(error.localizedDescription)")
            return
        }

        let targetURL = URL(string: "http://127.0.0.1:\(serverPort)/?platform=macos")!
        let probeURL = URL(string: "http://127.0.0.1:\(serverPort)/api/canvas")!

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var ready = false
            for _ in 0..<50 {
                var req = URLRequest(url: probeURL)
                req.timeoutInterval = 0.5
                let sem = DispatchSemaphore(value: 0)
                URLSession.shared.dataTask(with: req) { _, resp, _ in
                    if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                        ready = true
                    }
                    sem.signal()
                }.resume()
                sem.wait()
                if ready { break }
                Thread.sleep(forTimeInterval: 0.15)
            }
            DispatchQueue.main.async {
                guard let self = self else { return }
                if ready {
                    self.webView.load(URLRequest(url: targetURL))
                } else {
                    self.showStartupError("本地创作服务启动超时，请检查 Python 3 环境。")
                }
            }
        }
    }

    private func showStartupError(_ message: String) {
        let html = """
        <!doctype html><html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#08090e;color:#fca5a5;font-family:-apple-system,sans-serif;padding:24px;text-align:center;">
          <div><h2>启动遇到问题</h2><p>\(message)</p></div>
        </body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    // MARK: - WKUIDelegate (Open external links, File Upload Dialog, JS Alerts/Confirms)
    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if let url = navigationAction.request.url {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping ([URL]?) -> Void
    ) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping () -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = "Colab Canvas Studio"
        alert.informativeText = message
        alert.addButton(withTitle: "确定")
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (Bool) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = "确认操作"
        alert.informativeText = message
        alert.addButton(withTitle: "确定")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { resp in
            completionHandler(resp == .alertFirstButtonReturn)
        }
    }

    // MARK: - WKNavigationDelegate & Download Support
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        if navigationAction.shouldPerformDownload {
            decisionHandler(.download)
            return
        }
        if let url = navigationAction.request.url,
           let host = url.host,
           host != "127.0.0.1" && host != "localhost" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(
        _ download: WKDownload,
        decideDestinationUsing response: URLResponse,
        suggestedFilename: String,
        completionHandler: @escaping (URL?) -> Void
    ) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.beginSheetModal(for: window) { resp in
            completionHandler(resp == .OK ? panel.url : nil)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = StudioAppDelegate()
app.delegate = delegate
app.run()
