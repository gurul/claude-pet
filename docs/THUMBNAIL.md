# Generating the project thumbnail

Target uses and sizes:

| Use | Size | Where it goes |
|---|---|---|
| GitHub social preview | **1280×640** (2:1, <1MB PNG) | repo → Settings → General → Social preview |
| README hero | same image, referenced at top of README | `docs/assets/thumbnail.png` |

## Option A — real photo (recommended, it's a hardware project)

The actual device is the best thumbnail. Recipe:

1. Put the pet in a photogenic state: run the bridge, then force **attention**
   (orange LED + impatient pet) or **heart**:
   ```bash
   python3 - <<'EOF'
   import serial, glob, time, json
   p = serial.Serial(glob.glob('/dev/cu.usbmodem*')[0], 115200); p.dtr = True; time.sleep(0.5)
   p.write((json.dumps({"total":1,"running":1,"waiting":1,"msg":"needs approval",
     "prompt":{"id":"photo","tool":"Bash","hint":"git push origin main"}})+"\n").encode())
   EOF
   ```
   (No daemon running while you do this — it owns the port. `launchctl unload
   ~/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist` first, reload after.)
2. Shoot on a desk next to a terminal showing Claude Code, slight 3/4 angle,
   screen filling ~40% of frame. Dim room + screen brightness ~60% avoids blown
   highlights; tap the screen mid-animation for the cat's attention pose.
3. Crop to 2:1, export 1280×640 PNG → `docs/assets/thumbnail.png`.

## Option B — AI-generated (no photo gear needed)

Prompt for any capable image model (Ideogram/DALL·E/Midjourney):

> Flat-lay product shot of a small black ESP32 dev board with a 2.8-inch
> touchscreen standing on a wooden desk beside a laptop showing a dark terminal.
> The little screen shows a cute ASCII-art cat with an orange "approve?" prompt
> box and two tap buttons. A tiny orange LED glows on the board. Warm desk lamp
> lighting, shallow depth of field, cozy developer-desk aesthetic. Wide 2:1
> composition, space on the right for a title.

Then overlay text (optional): `claude pet` in a monospace font, bottom-right.

## Option C — pure screen capture composite

Photograph just the display straight-on (or grab the sprite from a video frame),
place it on a dark background with the repo name and the state icons
(sleep · idle · busy · attention · celebrate · dizzy · heart) in a row beneath.
Cheapest to make look clean; loses the "real hardware" proof.

## Applying it

```bash
gh api -X POST repos/gurul/claude-pet/social-preview  # not supported via API —
# upload manually: GitHub → claude-pet → Settings → Social preview → Upload
```

Social preview upload is UI-only. For the README: add
`![claude pet](docs/assets/thumbnail.png)` under the title.
