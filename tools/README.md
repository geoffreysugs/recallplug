# tools/

Developer tools. None of them is needed to use Recall Plug.

| Tool | What it does | Writes |
|---|---|---|
| `python -B -m tools.live_smoke [N ...]` | Live check on a real Windows desktop: (1) self-check against the local Ollama, (2) the recap window (found with EnumWindows: visible, topmost, bottom right, closes itself), (3) Ctrl+Alt+P sent with SendInput to an engine with fake titles, (4) a recap from the real Ollama with fake titles, (5) three questions to the ask feature. Prints a PASS / FAIL / SKIP table; exit code 1 on any FAIL. Pass step numbers to run only those (step 1 always runs). | Nothing |
| `python -B -m tools.make_screenshots [--png FILE] [--gif FILE] [--ollama] [--ask "question"]` | Scripted session of the Recall Plug window with FAKE window titles and a fake idle timer (mock backend, or the real Ollama with `--ollama`). Images come from PrintWindow on the app's own window, never a screen grab. Made `docs/gui.png` and `docs/demo.gif`. | Only the files you name |
| `python -m tools.vision_eval --images DIR --out DIR` | Vision model check on five screenshots (S1.png .. S5.png); refuses to write inside the repository. | Results outside the repository |
| `python -m tools.net_audit --minutes 10` | Starts `python -m app.main --demo` and records the network connections of that process tree every second (loopback or external); needs `psutil`. | `bench/net_audit/<timestamp>.txt` (counts and addresses only) |

## Dev-only dependency: Pillow

`--gif` needs [Pillow](https://pypi.org/project/pillow/); PNG files are written with the standard library. Pillow is **not** part of `requirements.txt` and the app never imports it. Install it in a throw-away virtual environment, for example:

```text
python -m venv %TEMP%\rp_tools_venv
%TEMP%\rp_tools_venv\Scripts\python -m pip install pillow
%TEMP%\rp_tools_venv\Scripts\python -B -m tools.make_screenshots --ollama --ask "Which contract file did I have open?" --gif docs\demo.gif
```

`tools.vision_eval` needs `psutil` (optional for the app, see `requirements.txt`).
