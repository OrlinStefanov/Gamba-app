> **This repository holds two apps.** Gamba is below. The other is
> **[Beach flag predictor](BEACHFLAG.md)** — tells you which safety flag a beach
> is likely flying from your location, live marine and weather models, and the
> surf-zone physics behind it. Standard library only: `./beach.sh "Bondi Beach"`,
> or publish `docs/` to GitHub Pages for an installable web app on your phone.

---

# Gamba

A desktop app that watches your screen and answers questions about it inside a
hard latency budget. Press a hotkey, get an answer in an always-on-top overlay —
by default it gives up and shows whatever it has after **4 seconds**.

Runs on Windows, macOS and Linux. Python 3.10+.

---

## Models and API keys

Gamba talks to **OpenAI** by default and can switch to **Anthropic** with one
flag. Both are streamed the same way and both obey the same deadline.

| Provider | Default model | Price per 1M tokens | Env var |
| --- | --- | --- | --- |
| `openai` (default) | `gpt-5.6-luna` | $0.20 in / $1.20 out | `OPENAI_API_KEY` |
| `anthropic` | `claude-haiku-4-5` | $1 in / $5 out | `ANTHROPIC_API_KEY` |

**Why `gpt-5.6-luna`:** it is the fast, low-cost tier of the GPT-5.6 family, it
accepts image input, and — the part that matters for a 4-second budget — it
supports `reasoning_effort: "none"`, which switches reasoning off entirely.
Reading a screen and answering is mostly extraction, not deliberation, so
reasoning tokens here are almost pure latency. Gamba sets `"none"` by default.
Raise it to `"low"` in the config if you are pointing it at something that
genuinely needs multi-step thinking, and raise `deadline_seconds` with it.

A typical ask is roughly **1,500 input + 40 output tokens ≈ $0.0003** — about
3,000 questions per dollar. The overlay shows a running total.

> **A ChatGPT subscription does not include API usage.** ChatGPT Plus/Pro and
> the API are billed separately. But if you have used **Agent Builder**, you
> already have a platform account, which *is* the API side — grab a key from
> [platform.openai.com/api-keys](https://platform.openai.com/api-keys) and make
> sure the organisation has credit on it. The same split applies on the
> Anthropic side: Claude Pro/Max does not cover API calls, so that provider
> needs its own key from
> [console.anthropic.com](https://console.anthropic.com/settings/keys).

---

## Setup

```sh
git clone <this repo> && cd Gamba-app

# Windows
setx OPENAI_API_KEY sk-proj-...        # then open a new terminal
run.bat

# macOS / Linux
export OPENAI_API_KEY=sk-proj-...
./run.sh
```

To run against Anthropic instead, export `ANTHROPIC_API_KEY` and either pass
`--provider anthropic` or set `"provider": "anthropic"` in the config:

```sh
python -m gamba --provider anthropic          # switches model and pricing too
python -m gamba --model gpt-5.6-terra          # keep the provider, change model
```

The launcher creates a virtualenv and installs dependencies on first run. To do
it by hand:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m gamba
```

Check everything is wired up:

```sh
python -m gamba doctor
```

**macOS** needs two permissions before hotkeys and capture work: System Settings
→ Privacy & Security → **Screen Recording** and **Accessibility**, both granted
to your terminal (or to the packaged app). **Linux** needs X11; hotkeys do not
work under Wayland.

---

## Using it

| Hotkey | What it does |
| --- | --- |
| `Ctrl+Alt+S` | Ask the standing question about the current screen |
| `Ctrl+Alt+A` | Type a custom question, `Enter` to send, `Esc` to cancel |
| `Ctrl+Alt+W` | Toggle continuous mode |
| `Ctrl+Alt+H` | Show/hide the overlay |
| `Ctrl+Alt+Q` | Quit |

The overlay is draggable — click and hold anywhere on it. The answer's first
line is rendered large; supporting detail sits underneath in grey.

**Continuous mode** re-asks by itself whenever the screen changes and then
settles, subject to a cooldown (6s by default) so a busy screen can't run up a
bill. It compares 64×64 grayscale thumbnails locally — no tokens are spent
deciding whether something changed.

Also usable from a terminal without the GUI:

```sh
python -m gamba ask "which option is correct?"
python -m gamba ask --deadline 2.5 --save-shot shot.jpg
```

---

## How it hits 4 seconds

The budget is enforced end-to-end, not hoped for:

- **Capture is off the critical path.** A background thread grabs and downscales
  a frame 4×/second, so pressing the hotkey costs a JPEG encode (~10ms) rather
  than a grab + resize (~50–150ms on a 4K display).
- **Small payload.** 1280px wide, JPEG quality 60 — enough to read UI text,
  small enough to upload fast and keep the token count near 1,500.
- **Reasoning off.** `reasoning_effort: "none"` on GPT-5.6 removes a variable
  and often multi-second thinking phase before the first token appears.
- **Streaming with an answer-first prompt.** The model is instructed to put the
  answer on the first line alone, so the useful part of the response lands in
  the first few hundred milliseconds of streaming.
- **A real deadline.** At the cutoff the app stops reading the stream and keeps
  the partial answer, flagged `truncated at deadline` — it never leaves you
  waiting past the budget.
- **No retries.** The SDK's automatic retries are off; a retry can't land inside
  the budget, so a failure is reported immediately instead of stalling.
- **Warm connection.** TLS is established at startup so the first ask doesn't
  pay for the handshake.

Superseded requests are cancelled: ask again while one is in flight and the old
one is dropped rather than racing to overwrite the display.

---

## Configuration

`python -m gamba init-config` writes the defaults to `~/.gamba/config.json`.
Every key is optional — missing ones fall back to the defaults.

```jsonc
{
  "capture": {
    "monitor": 1,          // 1 = primary, 0 = all displays stitched together
    "region": null,        // or {"left":0,"top":0,"width":1920,"height":1080}
    "max_width": 1280,     // lower = faster and cheaper, less readable text
    "jpeg_quality": 60,
    "fps": 4.0
  },
  "model": {
    "provider": "openai",         // or "anthropic"
    "model": "gpt-5.6-luna",
    "max_tokens": 300,
    "deadline_seconds": 4.0,
    "reasoning_effort": "none",   // OpenAI only; "" omits the parameter
    "image_detail": "auto",       // OpenAI only: auto | low | high
    "base_url": "",               // optional: Azure, a gateway, a local proxy
    "input_cost_per_mtok": 0.20,  // display only - update if prices move
    "output_cost_per_mtok": 1.20,
    "system_prompt": "...",       // rewrite this to change the answer format
    "api_key": ""                 // leave empty to use the env var
  },
  "watch": {
    "enabled_at_startup": false,
    "question": "What is the question on screen, and what is the answer?",
    "interval": 1.0,
    "change_threshold": 6.0,  // mean pixel delta (0-255) that counts as a change
    "cooldown": 6.0
  },
  "hotkeys": { "ask": "<ctrl>+<alt>+a", "quick": "<ctrl>+<alt>+s" },
  "ui": { "width": 460, "x": -40, "y": -40, "opacity": 0.88 }
}
```

Hotkey strings use [pynput syntax](https://pynput.readthedocs.io/en/latest/keyboard.html#global-hotkeys).
UI `x`/`y` are measured from the right/bottom edge when negative.

**Tuning knobs worth knowing:**

- Answers cut off? Raise `deadline_seconds` or `max_tokens`.
- Want it cheaper? Drop `max_width` to 1024 and `max_tokens` to 150.
- Text unreadable to the model? Raise `max_width` and `jpeg_quality`, set
  `image_detail` to `"high"`, or set a `region` around just the part of the
  screen that matters.
- Need more accuracy on hard questions? Move up a tier (`gpt-5.6-terra`,
  `gpt-5.6-sol`) or raise `reasoning_effort` to `"low"` — and raise
  `deadline_seconds` to match, since both cost time.
- Using a model outside the GPT-5 family (`gpt-4.1-mini`)? Set
  `reasoning_effort` to `""`, or the request is rejected with a 400.
- Want a different answer style? Rewrite `system_prompt` — it's the whole
  contract, and the first line of the reply is what the overlay shows large.

---

## Layout

```
gamba/
  capture.py             background screen capture, downscaling, change detection
  provider.py            shared result types + the provider factory
  openai_provider.py     OpenAI vision call, streaming, deadline, error mapping
  anthropic_provider.py  the same against Claude
  overlay.py             always-on-top tkinter HUD (thread-safe command queue)
  hotkeys.py             global hotkeys (pynput)
  app.py                 wiring: capture + hotkeys + continuous mode + overlay
  config.py              JSON config with per-provider defaults
tests/                   unit tests (no screen, key or network required)
```

Each provider module imports its SDK lazily, so only the one you actually use
has to be installed.

Run the tests with `python -m unittest discover -s tests`.

---

## Notes

- Screenshots are sent to Anthropic's API to be answered and are not stored by
  this app. Whatever is on screen when you press the hotkey goes with them —
  close anything you don't want transmitted.
- Whether using an assistant like this is appropriate depends on where you point
  it; exams, interviews and licensed games generally have rules about it.
