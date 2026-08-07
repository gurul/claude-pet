# Setup issues found during a from-scratch install

Recorded 2026-08-07 while bringing up the bridge on a clean machine (macOS 26.5,
Freenove FNK0104B on USB serial, era-code-wrapped Claude Code, Willow Voice).

Everything below is a real failure encountered in order, with the evidence that
identified it. The common thread: **every one of these fails silently.** Nothing
crashed, nothing printed an error at the point of failure — the pet just sat
there working-but-inert, which is the hardest possible thing to debug.

---

## 1. `install --service` silently defaults to BLE

**Severity: high — this alone makes a USB board look dead.**

`cc-buddy-bridge install --service` without `--serial-port` bakes a BLE-transport
service. On a USB-serial board the daemon scans Bluetooth forever:

```
INFO cc_buddy_bridge.ble: no buddy device found, retrying in 3.0s (miss #1)
```

The board is enumerated, powered, and healthy the entire time. Nothing indicates
the transport was chosen wrongly.

**Fix options:** auto-detect an attached supported board by USB VID at install
time and default to serial; or refuse to install a BLE service when a known board
is present on serial without an explicit `--transport ble`.

**Owner:** `claude-pet` — `bridge/src/cc_buddy_bridge/_service_launchd.py:51-54`,
`cli.py:39-52`

---

## 2. `install --service` skips hook installation entirely

**Severity: high**

`--service` is documented as installing a service *"instead of registering
hooks"* (`cli.py:46`), and the dispatch is a hard either/or (`cli.py:147-152`).
Running the obvious one-liner leaves you with a running daemon and **zero hooks**
— the board connects and shows battery, so it looks installed, but no session
state ever reaches it.

`cc-buddy-bridge status` does report `no cc-buddy-bridge hooks installed`, but
only if you think to run it.

**Fix options:** make `--service` additive (install hooks *and* the service, which
is what essentially every user wants); or print a loud "hooks NOT installed — run
`cc-buddy-bridge install`" line at the end of a `--service` run.

**Owner:** `claude-pet` — `bridge/src/cc_buddy_bridge/cli.py:147-152`

---

## 3. `voice-check` reports a trust result that does not apply to the daemon

**Severity: high — this one actively sent debugging in the wrong direction.**

`voice-check` run from a terminal reports the trust of *that* process. macOS
attributes Accessibility to the responsible process, so running it from a granted
terminal (Warp, iTerm) returns:

```
accessibility trusted: True
```

…while the launchd daemon, which has no responsible app and is evaluated as the
bare ad-hoc-signed interpreter, simultaneously logs:

```
WARNING cc_buddy_bridge.voice_trigger: key: Accessibility not granted
```

Both readings were true at the same moment. The CLI reported success for a
permission the daemon did not have, and we acted on it — twice.

**Fix options:** have `voice-check` query the *running daemon* over IPC for its
own trust state (add a `{"evt":"voice_trust"}` handler) and report that as the
headline number; demote the in-process check to a secondary line explicitly
labelled "this terminal, not the daemon".

**Owner:** `claude-pet` — `bridge/src/cc_buddy_bridge/voice_trigger.py:156-173`,
plus the `voice-check` CLI path

---

## 4. `launchctl kickstart -k` does not pick up plist edits

**Severity: medium — and `voice-check`'s own FIX text recommends it.**

`voice-check` ends with:

```
4. restart the daemon:
   launchctl kickstart -k gui/$(id -u)/com.github.cc-buddy-bridge.daemon
```

`kickstart` restarts the process but reuses the job definition cached at load
time. Any edited `EnvironmentVariables` are ignored. Observed directly:

| | |
|---|---|
| plist on disk | `CC_BUDDY_VOICE_HOTKEY=fn` |
| running process env | *(unset)* → fell back to `option` |

This produces a latent time-bomb: the running daemon behaves one way, and the
next login loads the plist fresh and behaves another, with no error either way.

**Fix:** use `launchctl unload && launchctl load -w` (or `bootout`/`bootstrap`)
wherever a config change is involved, and correct the `voice-check` FIX text.

**Owner:** `claude-pet` — `voice-check` output, `_service_launchd.py`

---

## 5. No way to persist the voice hotkey through `install --service`

**Severity: medium**

`--serial-port` is bakeable into the service; the voice hotkey is not
(`_service_launchd.py:51-54`). Setting it requires hand-editing the plist, and
the edit is destroyed by the next `install --service`, silently reverting to
`option`.

**Fix:** add `--voice-hotkey` to `install --service`, threaded exactly like
`--serial-port` (`cli.py` → `service.py` → `_build_plist`).

**Owner:** `claude-pet`

---

## 6. The Accessibility prompt fires at most once per daemon lifetime

**Severity: medium**

`voice_trigger.py:203,220` shows the system dialog once per daemon life
(`self._prompted`). This is correct — prompting on every failed hold would be
awful — but if the dialog is missed, dismissed, or appears while the user is
looking at the board rather than the screen, there is **no way to get it back**
short of restarting the daemon, and nothing says so.

The dialog is by far the easiest path to the grant (it has an "Open System
Settings" button that adds the entry directly), so losing it is costly. We
burned a long stretch on manual `+` / Cmd+Shift+G / drag attempts before
realising a daemon restart re-arms the prompt.

**Fix:** when a voice event arrives and trust is absent and the prompt was
already spent, log the actionable line: *"restart the daemon to re-show the
Accessibility prompt: launchctl kickstart -k …"*.

**Owner:** `claude-pet` — `bridge/src/cc_buddy_bridge/voice_trigger.py:203-220`

---

## 7. Manual Accessibility-grant paths are hostile, and the docs suggest the worst one

**Severity: low (documentation)**

Observed, in order of failure:

- **Drag from Finder into the list** — silently rejected. The list does not
  accept bare Unix executables on drop.
- **Cmd+Shift+G before clicking `+`** — does nothing; the shortcut only exists
  inside the file picker.
- **Cmd+Shift+G with focus in the picker's search field** — the path lands in
  the *search box*, which then searches "This Mac" for the literal string and
  returns nothing.

Only `+` → picker → *click out of the search field* → Cmd+Shift+G → paste works,
and `~/.local` being hidden means browsing to it is impossible.

**Fix:** point the FIX text at the system prompt (issue 6) as the primary route
and describe the `+` sequence with the focus caveat as the fallback.

**Owner:** `claude-pet` — `voice-check` output

---

## 8. Accessibility grants are keyed to an ad-hoc signature

**Severity: low, but a guaranteed future outage**

`voice-check` already calls this out well:

```
code signature: ad-hoc / linker-signed
  note: ad-hoc-signed interpreters (uv/pyenv builds) are the
  usual cause of a grant that 'won't stick'
```

Consequence: a `uv tool upgrade` that pulls a new interpreter build invalidates
the grant, and the pet goes quiet with no error. Worth a line in the README's
troubleshooting section, since the symptom is indistinguishable from every other
silent failure here.

**Owner:** `claude-pet` — README / docs

---

## 9. `DEFAULT_HOTKEY` contradicts its own docstring

**Severity: trivial**

`voice_trigger.py:9-11` states *"`fn` for Willow Voice (default)"*, but
`DEFAULT_HOTKEY == "option"`. One of the two is wrong. (Empirically on this
machine `option` is what works with Willow, so the docstring is the error.)

**Owner:** `claude-pet`

---

## 10. Serial links inherit a BLE security warning

**Severity: trivial**

```
INFO cc_buddy_bridge.daemon: stick link: UNENCRYPTED — transcript sniffable!
```

Emitted on a USB serial link, where the "link" is a physical cable. The warning
is meaningful for BLE and noise for serial.

**Fix:** suppress or reword when the transport is serial.

**Owner:** `claude-pet`

---

## Non-issues, recorded so they aren't re-investigated

- **`~/.local/bin` not on `PATH`** after `uv tool install`. uv warns about it;
  the daemon is unaffected (launchd uses an absolute interpreter path). Only
  affects typing `cc-buddy-bridge` by hand. `uv tool update-shell` fixes it.
- **`voice_trigger.py` absent from site-packages.** Expected under an editable
  install — the module resolves to `bridge/src/cc_buddy_bridge/`.
- **One board reset mid-session.** The pre-reset event ring was dumped to the log
  and the daemon reconnected on its own. The crash-reporting and auto-recovery
  features worked as designed.
- **era-code hook collision.** era-code 3.19.1 preserved the cc-buddy hooks when
  regenerating `settings.json`; `governance` / `session-start` and all 8 cc-buddy
  hooks coexist. The mitigation described in `claude_home.py:88-94` holds.

---

## Final working configuration

| | |
|---|---|
| Transport | serial, `/dev/cu.usbmodem*` (glob survives replug renumbering) |
| Claude homes | `~/.era/era-code/claude-home` + `~/.claude` |
| Hooks | 8, in the era-code home's `settings.json` |
| Voice hotkey | `option` (default — no plist override) |
| Accessibility | granted to `…/uv/python/cpython-3.12.13-macos-aarch64-none/bin/python3.12` |

Reproducing this from scratch, in the order that actually works:

```bash
uv tool install --editable ./bridge
cc-buddy-bridge install --service --serial-port '/dev/cu.usbmodem*'
cc-buddy-bridge install          # separate — --service does NOT do this
# hold the pet once → approve the system Accessibility dialog
launchctl unload ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
launchctl load -w ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist
```
