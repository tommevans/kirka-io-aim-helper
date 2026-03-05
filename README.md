# kirka-io-aim-helper

Lightweight macOS overlay for Safari FPS games (for example [kirka.io](https://kirka.io)).

## What it does

- Draws a transparent vertical and horizontal line as a crosshair guide.
- Limits the overlay to the active Safari window instead of your full display.
- Uses click-through rendering, so Safari still receives mouse and keyboard input.
- Hides automatically when Safari is not the frontmost app.

## Defaults

- Vertical offset is tuned for your setup: `--offset-y 65` (already default).
- Line opacity: `0.25`
- Line thickness: `1.5`
- Line color: `0,255,140`

## Requirements

- macOS
- Python 3.10+

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 crosshair_overlay.py
```

When running, a `Crosshair` item appears in the macOS menu bar. Use it to quit.

## Options

```bash
python3 crosshair_overlay.py --opacity 0.2 --thickness 2 --color "#00FF88" --offset-y 65
```

- `--opacity`: `0.0` to `1.0` (higher = more visible)
- `--thickness`: line thickness in points
- `--color`: `#RRGGBB` or `R,G,B`
- `--offset-x`: horizontal adjustment in points (`+` right, `-` left)
- `--offset-y`: vertical adjustment in points (`+` down, `-` up)

## Quick alignment tips

- Move down 10 points: `--offset-y 75`
- Move up 10 points: `--offset-y 55`
- Nudge right 5 points: `--offset-x 5`

## Troubleshooting

- If nothing appears, make sure Safari is the active/frontmost app.
- If window tracking is inaccurate, grant Screen Recording permission to your terminal/python host in `System Settings -> Privacy & Security -> Screen Recording`.
