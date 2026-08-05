// voicekey — a tiny, TCC-stable helper that holds Option+Space.
//
// Why this exists: macOS grants Accessibility per code signature. The bridge
// daemon runs on a uv-managed python that is "linker-signed" with
// Identifier=-, i.e. no stable identity, so a grant to it does not survive —
// it works once and then silently vanishes from the Accessibility list. A
// signed .app bundle with a fixed CFBundleIdentifier is what TCC is designed
// around, so the grant sticks.
//
// It also fixes attribution: launched via LaunchServices (`open -a`), this is
// its own responsible process, so TCC attributes the event post to THIS
// bundle rather than to whatever spawned it.
//
// Protocol: a Unix socket at /tmp/claude-pet-voicekey.sock, one ASCII command
// per line — "down", "up", "ping", "quit". Replies "ok\n" / "trust:0\n".
// Safety: any client disconnect releases the keys, and a watchdog releases
// after MAX_HOLD_SECS. A stuck system-wide Opt+Space is unacceptable.

import ApplicationServices
import Darwin
import Foundation

let SOCK_PATH = "/tmp/claude-pet-voicekey.sock"
let KEY_SPACE: CGKeyCode = 49
let KEY_OPTION: CGKeyCode = 58
let MAX_HOLD_SECS: Double = 60.0

var isHeld = false
var heldSince = Date.distantPast
let lock = NSLock()

func post(_ key: CGKeyCode, _ down: Bool, option: Bool) {
    guard let ev = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: down) else { return }
    if option { ev.flags = .maskAlternate }
    ev.post(tap: .cghidEventTap)
}

func holdDown() {
    lock.lock(); defer { lock.unlock() }
    if isHeld { return }
    post(KEY_OPTION, true, option: false)
    post(KEY_SPACE, true, option: true)
    isHeld = true
    heldSince = Date()
    FileHandle.standardError.write("voicekey: Opt+Space DOWN\n".data(using: .utf8)!)
}

func holdUp() {
    lock.lock(); defer { lock.unlock() }
    if !isHeld { return }
    post(KEY_SPACE, false, option: true)
    post(KEY_OPTION, false, option: false)
    isHeld = false
    FileHandle.standardError.write("voicekey: Opt+Space UP\n".data(using: .utf8)!)
}

// Watchdog: a lost "up" (client crash, board reset mid-hold) must not leave
// the modifier down system-wide.
Thread.detachNewThread {
    while true {
        Thread.sleep(forTimeInterval: 1.0)
        lock.lock()
        let stuck = isHeld && Date().timeIntervalSince(heldSince) > MAX_HOLD_SECS
        lock.unlock()
        if stuck {
            FileHandle.standardError.write("voicekey: watchdog release\n".data(using: .utf8)!)
            holdUp()
        }
    }
}

// Prompt for Accessibility on first launch; keep running either way so the
// client can query trust and tell the user what to fix.
let trusted = AXIsProcessTrustedWithOptions(
    [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
FileHandle.standardError.write("voicekey: trusted=\(trusted)\n".data(using: .utf8)!)

unlink(SOCK_PATH)
let fd = socket(AF_UNIX, SOCK_STREAM, 0)
guard fd >= 0 else { exit(1) }
var addr = sockaddr_un()
addr.sun_family = sa_family_t(AF_UNIX)
_ = withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
    SOCK_PATH.withCString { src in
        strncpy(UnsafeMutableRawPointer(ptr).assumingMemoryBound(to: CChar.self), src, 103)
    }
}
let addrLen = socklen_t(MemoryLayout<sockaddr_un>.size)
guard withUnsafePointer(to: &addr, { p in
    p.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, addrLen) }
}) == 0 else { exit(1) }
chmod(SOCK_PATH, 0o600)
listen(fd, 4)
FileHandle.standardError.write("voicekey: listening on \(SOCK_PATH)\n".data(using: .utf8)!)

while true {
    let client = accept(fd, nil, nil)
    if client < 0 { continue }
    var buf = [UInt8](repeating: 0, count: 256)
    var carry = ""
    while true {
        let n = read(client, &buf, buf.count)
        if n <= 0 { break }
        carry += String(bytes: buf[0..<n], encoding: .utf8) ?? ""
        while let nl = carry.firstIndex(of: "\n") {
            let cmd = String(carry[carry.startIndex..<nl]).trimmingCharacters(in: .whitespaces)
            carry = String(carry[carry.index(after: nl)...])
            var reply = "ok\n"
            switch cmd {
            case "down":
                if AXIsProcessTrusted() { holdDown() } else { reply = "trust:0\n" }
            case "up":   holdUp()
            case "ping": reply = AXIsProcessTrusted() ? "trust:1\n" : "trust:0\n"
            case "quit": holdUp(); close(client); close(fd); unlink(SOCK_PATH); exit(0)
            default:     reply = "err\n"
            }
            _ = reply.withCString { write(client, $0, strlen($0)) }
        }
    }
    holdUp()   // client vanished mid-hold — never leave keys down
    close(client)
}
