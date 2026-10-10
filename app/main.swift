// Jarvis.app — janela flutuante do beagle + liga o jarvisd (voz, microfone, fila de perguntas).
import Cocoa
import WebKit

let base = NSString(string: "~/Jarvis").expandingTildeInPath
let largura: CGFloat = 430
let altura: CGFloat = 420

// painel que pode receber teclado (campo de resposta escrita) sem ativar o app
final class Painel: NSPanel {
    override var canBecomeKey: Bool { true }
}

final class Delegado: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    var painel: Painel!
    var areas: [CGRect] = []   // onde o beagle/lista estão (coordenadas da página, origem no topo)
    var web: WKWebView!
    var jarvisd: Process?

    func applicationDidFinishLaunching(_ n: Notification) {
        ligarJarvisd()

        painel = Painel(contentRect: NSRect(x: 0, y: 0, width: largura, height: altura),
                         styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        painel.level = .statusBar
        painel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        painel.isOpaque = false
        painel.backgroundColor = .clear
        painel.hasShadow = false
        painel.ignoresMouseEvents = true   // por padrão o clique passa para o que está atrás
        painel.becomesKeyOnlyIfNeeded = true
        painel.hidesOnDeactivate = false

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "jarvis")
        web = WKWebView(frame: painel.contentView!.bounds, configuration: config)
        web.autoresizingMask = [.width, .height]
        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = self
        painel.contentView!.addSubview(web)
        posicionar()
        carregar()

        // só segura o clique quando o mouse está em cima do beagle, do número ou da lista
        Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { [weak self] _ in self?.atualizarClique() }

        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                               object: nil, queue: .main) { [weak self] _ in self?.posicionar() }
    }

    func posicionar() {
        guard let tela = NSScreen.main?.visibleFrame else { return }
        painel.setFrameOrigin(NSPoint(x: tela.maxX - largura - 8, y: tela.minY + 8))
    }

    func carregar() {
        web.load(URLRequest(url: URL(string: "http://127.0.0.1:8765/")!))
    }

    // página carregou: o beagle aparece (a página esconde depois, se ele estiver dispensado)
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) {
        posicionar()
        painel.orderFrontRegardless()
    }

    // o jarvisd ainda não subiu: tenta de novo
    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.carregar() }
    }

    func atualizarClique() {
        guard painel.isVisible else { return }
        let m = NSEvent.mouseLocation
        let f = painel.frame
        let ponto = CGPoint(x: m.x - f.minX, y: f.height - (m.y - f.minY))  // origem no topo, como a página
        let dentro = areas.contains { $0.contains(ponto) }
        if painel.ignoresMouseEvents == dentro { painel.ignoresMouseEvents = !dentro }
    }

    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let corpo = m.body as? [String: Any] else { return }
        if let visivel = corpo["visivel"] as? Bool {
            if visivel { posicionar(); painel.orderFrontRegardless() } else { painel.orderOut(nil) }
        }
        if let lista = corpo["areas"] as? [[Double]] {
            areas = lista.filter { $0.count == 4 }.map { CGRect(x: $0[0], y: $0[1], width: $0[2], height: $0[3]) }
        }
        if corpo["foco"] != nil { painel.makeKey() }   // campo de resposta escrita
        if let erro = corpo["erro"] as? String {         // erro na página: registra no log do Jarvis
            let linha = "erro na tela: \(erro)\n"
            if let h = FileHandle(forWritingAtPath: base + "/jarvis.log") {
                h.seekToEndOfFile(); h.write(linha.data(using: .utf8)!); h.closeFile()
            }
        }
    }

    func ligarJarvisd() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: base + "/venv-ww/bin/python")
        p.arguments = [base + "/jarvisd.py"]
        p.currentDirectoryURL = URL(fileURLWithPath: base)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin"
        p.environment = env
        let saida = FileHandle(forWritingAtPath: base + "/jarvisd.out") ?? FileHandle.nullDevice
        p.standardOutput = saida
        p.standardError = saida
        p.terminationHandler = { [weak self] _ in
            // caiu? sobe de novo em 3 s
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self?.ligarJarvisd() }
        }
        try? p.run()
        jarvisd = p
    }

    func applicationWillTerminate(_ n: Notification) {
        jarvisd?.terminationHandler = nil
        jarvisd?.terminate()
    }
}

let app = NSApplication.shared
let delegado = Delegado()
app.delegate = delegado
app.setActivationPolicy(.accessory)
app.run()
