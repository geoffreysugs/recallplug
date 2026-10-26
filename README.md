# RecallPlug

**Your “where was I?”, prepared while you are away.**

RecallPlug is a small Windows tool that answers one question the moment you sit back down: *what was I working on?* While you are away from your desk, a local language model prepares a three-line recap. When you return, it appears in a translucent window at the bottom right of the screen.

![The recap window, shown after the user returns](docs/hud.png)

Entry for the **ASUS UGen AI League**: Battlefield Lightning · Workplace AI · Onsite track. Target hardware: **ASUS UGen300 USB AI Accelerator** (Hailo-10H). The prototype runs on a laptop with Ollama today; the UGen300 backend is planned for Stage II.

## Why

Interruptions are normal in office work, and getting back to the task takes time. In a field study of 36 workers, resuming an interrupted task took about 25 minutes on average, including time spent on other work [1].

Memory tools for the desktop exist, but they make a different trade-off. Windows Recall needs a Copilot+ PC (an NPU of 40+ TOPS, 16 GB RAM, 256 GB storage) and takes screen snapshots every few seconds [2]. RecallPlug starts from the other end: the **current prototype uses window titles only**, keeps them **in memory**, and runs a **local model** on an ordinary Windows PC. A content layer that reads the screen is planned, with a single snapshot when you leave and strict privacy conditions (see below).

## How it works

1. **Work.** Window titles are logged in memory (after a block list and masking).
2. **Idle.** No keyboard or mouse input for 5 minutes (10 seconds in demo mode).
3. **Recap.** A local model prepares three lines in the background.
4. **Return.** The recap appears at the bottom right, already written.

Real output of the current prototype (title layer):

```text
• You stopped in Contract Draft v2.txt (Notepad)
• You were drafting a contract.
• Next: Finish editing Contract Draft v2.txt.
```

**Facts by rules, reasoning by model.** Line 1 (where you stopped) is rule-based, so it is always correct. Lines 2 and 3 (what you were doing, a next step) come from the model and are checked: they must be grounded in your recent windows, they must not suggest irreversible actions (delete, uninstall, send, pay, purchase, submit), and a line that fails a check is replaced by a rule-based sentence. A small model is good at themes and weak at counting windows, so each job sits where it works.

## Content layer (planned, Stage II)

Window titles cannot tell you which quote you were reading. The content layer adds a short text description of the foreground window, with three triggers that share one pipeline:

| Trigger | Condition | Level |
|---|---|---|
| Away snapshot | Idle for 5 minutes: capture once, describe while you are away | Commitment |
| Reading pause snapshot | Input idle for 10 s with the same title, at most 2 descriptions a minute on average; on by default, built in the finals phase | Target |
| Thumbnail watch | Every 5 s, compare a tiny grayscale image on the CPU | Later |

The pipeline: a block-listed title means no capture at all; only the foreground window is captured, in memory; a vision model describes type, topic and structure (no numbers or names); personal data is masked, the image is discarded and only text is kept. Descriptions are shown with the label “AI description, may be wrong”.

**Ship condition:** the content layer is on by default only when six protections exist: pause key, capture indicator, editable block list, summary passed over standard input (not the command line), no display while the screen is locked, and automated network and no-image-on-disk tests. If any is missing, the layer starts switched off. None of this is built yet.

## Privacy (today)

- Only window titles are used. No screenshots, no screen text.
- Titles stay in memory. They are never written to disk, and neither are the model's answers.
- Windows whose titles suggest banking, passwords or private browsing are skipped. Email addresses, phone numbers, card numbers and ID numbers are masked before text reaches the model.
- Inference runs locally. No cloud service is part of the pipeline, by design. A network audit has not been completed yet.
- Limits: titles can still contain sensitive words. The summary is currently handed to the recap window as a command-line argument, which other programs of the same user can read for the 15 seconds the window exists; passing it over standard input is planned.

## Architecture

<!-- Add your own architecture diagram as docs/architecture.png, then remove this comment markup:
![Architecture: watcher, privacy gate, memory, recap engine, inference backend, HUD](docs/architecture.png)
-->

All inference sits behind one interface. `OllamaBackend` is built; a mock backend is used in tests; the UGen300 (Hailo) backend is a stub that reports “not supported yet” and falls back to Ollama. Porting to the device should not change the rest of the program.

## Hardware and models

| Function | Model | Platform | Status |
|---|---|---|---|
| Away recap (text) | qwen2.5:1.5b via Ollama | Windows 11 laptop | Built |
| Away recap (text) | Qwen2.5-1.5B-Instruct | ASUS UGen300 (Hailo-10H) | Planned (commitment) |
| Screen description (vision) | Qwen2.5-VL 3B via Ollama, stand-in model | Windows 11 laptop | Planned (commitment) |
| Screen description (vision) | Qwen2-VL-2B-Instruct | ASUS UGen300 (Hailo-10H) | Planned |

UGen300 datasheet: 40 TOPS (INT4) / 20 TOPS (INT8), 8 GB LPDDR4, 2.5 W typical, USB 3.1 Gen2 Type-C [3].

## Results

**MEASURED on a laptop** (30 runs per scenario, 2026-10-25, Intel i7-12650H, RTX 4060 Laptop, 64 GB RAM, Windows 11, Ollama, warm model, plugged in, fake English windows):

| Metric | office | web_heavy |
|---|---|---|
| Model call, median (p95) | 0.184 s (0.202) | 0.149 s (0.190) |
| Output speed, median | 180.7 tokens/s | 185.7 tokens/s |
| Host CPU, system-wide, median | 11.2% | 9.4% |
| Host RAM, system-wide | 19.5% | 19.5% |

**PROJECTED for UGen300** from the datasheet benchmarks [3]: about 4.0 s for a 25-token text recap (0.37 s first token, 6.82 tokens/s) and about 8.4 s for a 50-token vision description (0.93 s first token, 6.73 tokens/s; the 50 tokens is an assumption). These are estimates, not measurements.

Notes: CPU and RAM are whole-computer averages during the call. The laptop has a discrete GPU, so these numbers do not show the value of an accelerator; a CPU-only comparison is planned. At a low temperature the outputs are near-identical across runs, so the check pass rate shows format compliance, not quality. Raw results are in `bench/results/`.

## Status

- [x] Window-title logging (memory only) and idle detection
- [x] Away-to-return flow and recap window
- [x] Local recap: rule-based line 1, checked lines 2 and 3
- [x] Privacy masking and block list for titles
- [x] Inference backend interface (Ollama, mock)
- [x] Benchmark script with two scenarios, and 112 automated tests
- [ ] Content layer: away snapshot with a vision model (Stage II, commitment)
- [ ] Pause key, capture indicator, one-click clear, editable block list (commitment)
- [ ] UGen300 backend and on-device benchmark: PROJECTED becomes MEASURED (commitment)
- [ ] Reading pause snapshot (on by default), 30-minute digest, manual pins, export and import (Stage II, target)
- [ ] Network audit and no-image-on-disk tests, standard-input hand-off, no display when locked (ship conditions)

## Quick start

Requirements: Windows 11, Python 3.12 and [Ollama](https://ollama.com) running locally with the model pulled (`ollama pull qwen2.5:1.5b`). `pip install -r requirements.txt` is optional (`psutil`, for CPU and RAM numbers).

```text
python -m app.main                       # run with real timers (away after 5 min idle)
python -m app.main --preview 15          # log windows for 15 s, then show the recap now
python -m app.main --demo                # real flow, "away" shortened to 10 s
python -m app.main --preview 25 --debug  # also print the raw model answer and why a line was replaced (terminal only, never saved)
python -m app.main --backend mock        # ollama (default) | mock | hailo (Stage II stub)
python -m app.main --no-model            # rule-based lines only, no model call
python -m app.hud --sample               # show the recap window with sample text

python -m bench.run_bench --runs 30      # benchmark both fake scenarios (office, web_heavy)
python -m bench.run_bench --runs 10 --show-rejections
python -m bench.run_bench --runs 10 --no-model
python -m bench.run_bench --runs 10 --ground-windows 2   # strict line 3 check

python -m unittest -v                    # run the tests
```

Benchmark results are saved to `bench/results/<timestamp>/` (`results.csv`, `summary.md`); `bench/summary.md` is the latest summary. No window titles or model answers are written to any file.

## Limitations

- A 1.5B model gives modest next-step suggestions. When recent windows are web pages, it tends to continue the main document rather than the last page.
- The current prototype uses window titles only, so vague titles give vague recaps.
- Tested on one laptop, mostly with fake English windows. Other screens, scaling and multi-monitor setups are untested.
- The UGen300 backend is not implemented yet, and Windows support for the device's generative runtime is not confirmed.

## Roadmap

- **Stage I (by 2026-10-28):** prototype, tests, benchmark, public repository.
- **2026-11-06 to 12-04 (Stage II, planned):** confirm the runtime path, UGen300 backend and on-device benchmark, content layer (away snapshot) with its six protections, then reading pause snapshot, digest, pins and export.
- **2026-12-10 / 12-19:** final deck, then the final at ASUS HQ.

## References

1. Mark, Gonzalez, Harris. “No Task Left Behind? Examining the Nature of Fragmented Work.” CHI 2005.
2. Microsoft. Copilot+ PCs and Recall requirements. https://microsoft.com/en-ie/windows/copilot-plus-pcs
3. ASUS. UGen300 USB AI Accelerator datasheet (UGen300-USB-8G); benchmark figures from Hailo, retrieved 02/11/26.

## About

Built by geoffrey (GitHub: geoffreysugs) for the ASUS UGen AI League. License: not chosen yet.
