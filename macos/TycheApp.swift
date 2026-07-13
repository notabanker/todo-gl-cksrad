import AppKit
import WebKit
import Darwin

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var statusView: NSVisualEffectView!
    private var titleLabel: NSTextField!
    private var detailLabel: NSTextField!
    private var retryButton: NSButton!
    private var logButton: NSButton!

    private var backend: Process?
    private var baseURL: URL?
    private var supportURL: URL!
    private var logURL: URL!
    private var logHandle: FileHandle?
    private var healthTask: Task<Void, Never>?
    private var monitorTimer: Timer?
    private var stopTimer: Timer?
    private var stopDeadline = Date.distantPast
    private var sentKill = false
    private var stopping = false
    private var quitting = false
    private var stopCompletion: (() -> Void)?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.appearance = NSAppearance(named: .aqua)
        do {
            supportURL = try makeSupportDirectory()
            logURL = supportURL.appendingPathComponent("backend.log")
        } catch {
            showFatalAlert("To-Do Gambling cannot create its data folder", detail: error.localizedDescription)
            NSApp.terminate(nil)
            return
        }

        buildMenus()
        buildWindow()
        NSApp.activate(ignoringOtherApps: true)
        startBackend()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        quitting = true
        healthTask?.cancel()
        guard backend?.isRunning == true else { return .terminateNow }
        stopBackend { sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }

    private func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1060, height: 720),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "To-Do Gambling"
        window.minSize = NSSize(width: 840, height: 560)
        window.center()
        window.setFrameAutosaveName("TYCHE.MainWindow.Compact.v2")

        let root = NSView()
        window.contentView = root

        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(webView)

        statusView = NSVisualEffectView()
        statusView.material = .underWindowBackground
        statusView.blendingMode = .withinWindow
        statusView.state = .active
        statusView.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(statusView)

        titleLabel = NSTextField(labelWithString: "Starting To-Do Gambling…")
        titleLabel.font = .systemFont(ofSize: 24, weight: .semibold)
        titleLabel.alignment = .center

        detailLabel = NSTextField(wrappingLabelWithString: "Preparing your wheel")
        detailLabel.textColor = .secondaryLabelColor
        detailLabel.alignment = .center
        detailLabel.maximumNumberOfLines = 5

        retryButton = NSButton(title: "Try Again", target: self, action: #selector(retry))
        retryButton.bezelStyle = .rounded
        retryButton.keyEquivalent = "\r"
        retryButton.isHidden = true

        logButton = NSButton(title: "Show Log", target: self, action: #selector(showLog))
        logButton.bezelStyle = .rounded
        logButton.isHidden = true

        let buttons = NSStackView(views: [retryButton, logButton])
        buttons.orientation = .horizontal
        buttons.spacing = 10
        buttons.alignment = .centerY

        let stack = NSStackView(views: [titleLabel, detailLabel, buttons])
        stack.orientation = .vertical
        stack.spacing = 14
        stack.alignment = .centerX
        stack.translatesAutoresizingMaskIntoConstraints = false
        statusView.addSubview(stack)

        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            webView.topAnchor.constraint(equalTo: root.topAnchor),
            webView.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            statusView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            statusView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            statusView.topAnchor.constraint(equalTo: root.topAnchor),
            statusView.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            stack.centerXAnchor.constraint(equalTo: statusView.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: statusView.centerYAnchor),
            stack.leadingAnchor.constraint(greaterThanOrEqualTo: statusView.leadingAnchor, constant: 40),
            stack.trailingAnchor.constraint(lessThanOrEqualTo: statusView.trailingAnchor, constant: -40),
            detailLabel.widthAnchor.constraint(lessThanOrEqualToConstant: 640)
        ])

        window.makeKeyAndOrderFront(nil)
    }

    private func buildMenus() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        let appName = (Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String) ?? "To-Do Gambling"
        appMenu.addItem(withTitle: "About \(appName)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let view = NSMenu(title: "View")
        let reload = NSMenuItem(title: "Reload", action: #selector(reloadPage), keyEquivalent: "r")
        reload.target = self
        view.addItem(reload)
        viewItem.submenu = view
        main.addItem(viewItem)
        NSApp.mainMenu = main
    }

    private func startBackend() {
        healthTask?.cancel()
        stopping = false
        showStarting()

        do {
            guard let executable = Bundle.main.url(
                forResource: "tyche-backend",
                withExtension: nil,
                subdirectory: "Backend"
            ) else {
                throw LauncherError.missingBackend
            }
            guard FileManager.default.isExecutableFile(atPath: executable.path) else {
                throw LauncherError.backendNotExecutable(executable.path)
            }

            let port = try availableLoopbackPort()
            let database = supportURL.appendingPathComponent("tyche.sqlite3")
            try installStarterDatabaseIfNeeded(at: database)
            let url = URL(string: "http://127.0.0.1:\(port)/")!
            baseURL = url

            FileManager.default.createFile(atPath: logURL.path, contents: nil)
            let output = try FileHandle(forWritingTo: logURL)
            try output.truncate(atOffset: 0)
            logHandle = output

            let process = Process()
            process.executableURL = executable
            process.currentDirectoryURL = executable.deletingLastPathComponent()
            process.arguments = [
                "--host", "127.0.0.1",
                "--port", String(port),
                "--db", database.path,
                "--no-browser"
            ]
            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONUNBUFFERED"] = "1"
            environment["TYCHE_DB_PATH"] = database.path
            process.environment = environment
            process.standardOutput = output
            process.standardError = output

            try process.run()
            backend = process
            beginMonitoring()
            waitUntilHealthy(url, timeout: 12)
        } catch {
            showFailure("To-Do Gambling couldn’t start", detail: error.localizedDescription)
        }
    }

    private func waitUntilHealthy(_ url: URL, timeout: TimeInterval) {
        healthTask?.cancel()
        healthTask = Task { [weak self] in
            guard let self else { return }
            let deadline = Date().addingTimeInterval(timeout)
            let healthURL = url.appendingPathComponent("healthz")
            while !Task.isCancelled, Date() < deadline {
                guard self.backend?.isRunning == true else {
                    self.showFailure("To-Do Gambling stopped while starting", detail: self.logHint())
                    return
                }
                var request = URLRequest(
                    url: healthURL,
                    cachePolicy: .reloadIgnoringLocalCacheData,
                    timeoutInterval: 0.6
                )
                request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
                do {
                    let (_, response) = try await URLSession.shared.data(for: request)
                    if (response as? HTTPURLResponse)?.statusCode == 200 {
                        guard !Task.isCancelled else { return }
                        self.statusView.isHidden = true
                        self.webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
                        return
                    }
                } catch {
                    // Expected while uvicorn/PyInstaller finishes starting.
                }
                try? await Task.sleep(nanoseconds: 180_000_000)
            }
            guard !Task.isCancelled else { return }
            self.showFailure("To-Do Gambling took too long to start", detail: "The local service did not become ready. \(self.logHint())")
        }
    }

    private func beginMonitoring() {
        monitorTimer?.invalidate()
        monitorTimer = Timer.scheduledTimer(
            timeInterval: 0.4,
            target: self,
            selector: #selector(checkBackend),
            userInfo: nil,
            repeats: true
        )
    }

    @objc private func checkBackend() {
        guard !stopping, !quitting, let process = backend else { return }
        guard !process.isRunning else { return }
        monitorTimer?.invalidate()
        monitorTimer = nil
        healthTask?.cancel()
        try? logHandle?.close()
        logHandle = nil
        backend = nil
        showFailure("To-Do Gambling stopped unexpectedly", detail: "Exit status: \(process.terminationStatus). \(logHint())")
    }

    @objc private func retry() {
        retryButton.isEnabled = false
        healthTask?.cancel()
        stopBackend { [weak self] in
            self?.retryButton.isEnabled = true
            self?.startBackend()
        }
    }

    private func stopBackend(completion: @escaping () -> Void) {
        healthTask?.cancel()
        monitorTimer?.invalidate()
        monitorTimer = nil
        guard let process = backend, process.isRunning else {
            backend = nil
            completion()
            return
        }

        stopping = true
        stopCompletion = completion
        stopDeadline = Date().addingTimeInterval(1.5)
        sentKill = false
        process.terminationHandler = { [weak self] finishedProcess in
            DispatchQueue.main.async {
                guard let self, self.backend === finishedProcess else { return }
                self.finishStop()
            }
        }
        process.terminate()
        let processIdentifier = process.processIdentifier
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 1.5) {
            if Darwin.kill(processIdentifier, 0) == 0 {
                Darwin.kill(processIdentifier, SIGKILL)
            }
        }
    }

    @objc private func checkBackendStopped() {
        guard let process = backend else {
            finishStop()
            return
        }
        if !process.isRunning {
            finishStop()
            return
        }
        if Date() >= stopDeadline {
            if !sentKill {
                Darwin.kill(process.processIdentifier, SIGKILL)
                sentKill = true
                stopDeadline = Date().addingTimeInterval(0.5)
            } else {
                finishStop()
            }
        }
    }

    private func finishStop() {
        stopTimer?.invalidate()
        stopTimer = nil
        backend?.terminationHandler = nil
        try? logHandle?.close()
        logHandle = nil
        backend = nil
        stopping = false
        let completion = stopCompletion
        stopCompletion = nil
        completion?()
        if quitting {
            Darwin.exit(EXIT_SUCCESS)
        }
    }

    @objc private func reloadPage() {
        if webView.url != nil {
            webView.reload()
        } else if let baseURL {
            webView.load(URLRequest(url: baseURL, cachePolicy: .reloadIgnoringLocalCacheData))
        } else {
            retry()
        }
    }

    @objc private func showLog() {
        NSWorkspace.shared.activateFileViewerSelecting([logURL])
    }

    private func showStarting() {
        statusView?.isHidden = false
        titleLabel?.stringValue = "Starting To-Do Gambling…"
        detailLabel?.stringValue = "Preparing your wheel"
        retryButton?.isHidden = true
        logButton?.isHidden = true
    }

    private func showFailure(_ title: String, detail: String) {
        statusView?.isHidden = false
        titleLabel?.stringValue = title
        detailLabel?.stringValue = detail
        retryButton?.isHidden = false
        retryButton?.isEnabled = true
        logButton?.isHidden = !FileManager.default.fileExists(atPath: logURL.path)
    }

    private func logHint() -> String {
        "See \(logURL.path) for details."
    }

    private func makeSupportDirectory() throws -> URL {
        let base = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        // Preserve the original support directory so a renamed app keeps all
        // existing tasks, completed wins, and reward history.
        let folder = base.appendingPathComponent("TYCHE", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }

    private func installStarterDatabaseIfNeeded(at database: URL) throws {
        guard !FileManager.default.fileExists(atPath: database.path) else { return }
        guard let starter = Bundle.main.url(forResource: "starter", withExtension: "sqlite3") else { return }
        try FileManager.default.copyItem(at: starter, to: database)
    }

    private func availableLoopbackPort() throws -> UInt16 {
        let descriptor = Darwin.socket(AF_INET, SOCK_STREAM, IPPROTO_TCP)
        guard descriptor >= 0 else { throw LauncherError.socket(errno) }
        defer { Darwin.close(descriptor) }

        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(0)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")

        let bindResult = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else { throw LauncherError.socket(errno) }

        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.getsockname(descriptor, $0, &length)
            }
        }
        guard nameResult == 0 else { throw LauncherError.socket(errno) }
        return UInt16(bigEndian: address.sin_port)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        showFailure("To-Do Gambling couldn’t load", detail: "\(error.localizedDescription) \(logHint())")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        if url.scheme == "about" || (url.host == baseURL?.host && url.port == baseURL?.port) {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    private func showFatalAlert(_ title: String, detail: String) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = title
        alert.informativeText = detail
        alert.runModal()
    }
}

private enum LauncherError: LocalizedError {
    case missingBackend
    case backendNotExecutable(String)
    case socket(Int32)

    var errorDescription: String? {
        switch self {
        case .missingBackend:
            return "The bundled backend was not found. Reinstall To-Do Gambling."
        case .backendNotExecutable(let path):
            return "The bundled backend is not executable: \(path)"
        case .socket(let code):
            return "Could not reserve a local port (errno \(code))."
        }
    }
}

@main
private struct TYCHEApplication {
    @MainActor
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        application.delegate = delegate
        application.setActivationPolicy(.regular)
        application.run()
    }
}
