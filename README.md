# RecallPlug

**Your “where was I?”, prepared while you are away.**

RecallPlug is a small Windows tool that answers one question the moment you sit back down: *what was I working on?* While you are away from your desk, a local language model prepares a three-line recap. When you return, it appears in a translucent window at the bottom right of the screen.

![The recap window, shown after the user returns](docs/hud.png)

Entry for the **ASUS UGen AI League**: Battlefield Lightning · Workplace AI · Onsite track. Target hardware: **ASUS UGen300 USB AI Accelerator** (Hailo-10H). The prototype runs on a laptop with Ollama today; the UGen300 backend is the first thing planned for Stage II.

## Why

Interruptions are normal in office work, and getting back to the task takes time. In a field study of 24 information workers, interrupted work that was resumed on the same day took about 25 minutes to come back to, after more than two other activities [1].

Memory tools for the desktop exist, but they make a different trade-off. Windows Recall needs a Copilot+ PC (an NPU of 40+ TOPS) and takes images of your active screen every few seconds [2]. Screenpipe records the screen and audio continuously and stores them locally [3]. RecallPlug starts from the other end: it looks at the window you are in, keeps only a short text note, and runs a **local model** on an ordinary Windows PC with a USB accelerator.

## Advantages

| Advantage | What it means |
|---|---|
| **Works offline** | After a one-time model download, no network is needed. |
| **No per-use token fees** | Every request runs on your own PC and the UGen300, so there is no cloud charge for each use, unlike cloud AI assistants. The UGen300 is a one-time hardware purchase. |
| **Private by design** | Only the window you are in is read, the image is deleted right after analysis, only a short text note is kept, and nothing is uploaded. |
| **Light on your PC** | The heavy AI work is meant to run on the UGen300, so an ordinary office PC does not have to be replaced. |

These are design goals. The offline behaviour, the privacy design and the work on the UGen300 are not yet verified on the device.

## How it works

1. **Work.** Window titles are logged in memory (after a block list and masking).
2. **Idle.** No keyboard or mouse input for 5 minutes (10 seconds in demo mode).
3. **Recap.** A local model prepares three lines in the background.
4. **Return.** The recap appears at the bottom right, already written. It is handed to the recap window over standard input, so it never appears on a command line.

Real output of the current prototype:

```text
• You stopped in Contract Draft v2.txt (Notepad)
• You were drafting a contract.
• Next: Finish editing Contract Draft v2.txt.
```

**Facts by rules, reasoning by model.** Line 1 (where you stopped) is written by rules. Lines 2 and 3 (what you were doing, a next step) come from the model and are checked: they must be grounded in your recent windows, they must not suggest irreversible actions (delete, uninstall, send, pay, purchase, submit), and a line that fails a check is replaced by a rule-based sentence. A small model is good at themes and weak at counting windows, so each job sits where it works.

## Architecture

**Target design:** light work stays on the PC; all AI models run on the UGen300. The models are stored on the PC and loaded into the device at each launch (the UGen300 datasheet lists 8 GB of memory and no storage). The only network use is a one-time model download, with your consent.

![Target architecture](docs/architecture.png)

**Stage I (today):** everything runs on the laptop; the model interface talks to Ollama. The UGen300 is not connected yet.

![Stage I architecture](docs/architecture_stage1.png)

All inference sits behind one interface (`OllamaBackend` and a mock backend for tests are built; the UGen300 backend is a stub that falls back to Ollama), so moving to the device should not change the rest of the program.

## Status

**Built (works in the prototype today)**

- Window-title logging in memory only, idle detection, away and return detection
- Local recap: rules write line 1, the model writes lines 2–3 and is checked
- Block list and masking of emails, phone numbers and card numbers
- Recap window at the bottom right, fed over standard input
- Model interface with Ollama and a mock backend, plus a CPU-only option for measurements
- Benchmark script, vision evaluation tool, and 168 automated tests

**Target (not built yet)**

- **High priority:** UGen300 backend with on-device measurements
- **High priority:** pause key, capture indicator, editable block list
- Reading the window after 10 s idle, checking every 10 s for changes, and keeping only a short text note (the image is deleted right after analysis)
- Vision description of the window; removing copied names
- Console with an ask panel; answers cite the saved recap they used
- Pins with notes, 30-minute digest, 60-minute archive
- Saving with encryption, export and import
- First-launch model download, with consent
- Network audit

High priority means built first in Stage II. The other targets follow as time allows.

## Hardware and models

| Function | Model | Platform | Status |
|---|---|---|---|
| Recap (text) | qwen2.5:1.5b via Ollama | Windows 11 laptop | Built |
| Recap and ask (text) | Qwen2.5-1.5B-Instruct | ASUS UGen300 (Hailo-10H) | Target |
| Screen description (vision) | Qwen2.5-VL 3B via Ollama, stand-in model | Windows 11 laptop | Tested by hand |
| Screen description (vision) | Qwen2-VL-2B-Instruct | ASUS UGen300 (Hailo-10H) | Target |

UGen300 datasheet: 40 TOPS (INT4) / 20 TOPS (INT8), 8 GB LPDDR4, 2.5 W typical, USB 3.1 Gen2 Type-C [4]. Model files are not part of this repository; they are meant to be downloaded by the app at first launch.

## Results

**MEASURED on a laptop** (30 runs per scenario, 2026-10-25, Intel i7-12650H, RTX 4060 Laptop, 64 GB RAM, Windows 11, Ollama, warm model, plugged in, fake English windows):

| Metric | office | web_heavy |
|---|---|---|
| Model call, median (p95) | 0.184 s (0.202) | 0.149 s (0.190) |
| Output speed, median | 180.7 tokens/s | 185.7 tokens/s |
| Host CPU, system-wide, median | 11.2% | 9.4% |
| Host RAM, system-wide | 19.5% | 19.5% |

**UGen300 datasheet (Hailo benchmark, not our measurement)** [4]:

| Model | Load | First token | Speed |
|---|---|---|---|
| Qwen2.5-1.5B (text) | 5.05 s | 0.37 s | 6.82 tokens/s |
| Qwen2-VL-2B (vision) | 6.23 s | 0.93 s (image) | 6.73 tokens/s |

The UGen300 outputs fewer tokens per second than this laptop's GPU. The recap is prepared before you return, so what matters is keeping the PC free. On-device numbers come in Stage II.

**Vision check on five screens (MEASURED, 2026-10-26, one laptop, stand-in model Qwen2.5-VL 3B, GPU off):** one description took 84–93 s with whole-machine CPU at 64–68% on average. Checked by hand, all five descriptions were correct and added information the window titles did not contain; the model still copied some names. GPU mode printed garbled output on this machine and is not used. The screenshots and descriptions are kept outside this repository.

Notes: CPU and RAM are whole-computer averages during the call. The laptop has a discrete GPU, so the first table does not show what an accelerator saves. At a low temperature the outputs are near-identical across runs, so the check pass rate shows format compliance, not quality. Raw results are in `bench/results/`.

## Quick start

Requirements: Windows 11, Python 3.12 and [Ollama](https://ollama.com) running locally with the model pulled (`ollama pull qwen2.5:1.5b`). `pip install -r requirements.txt` is optional (`psutil`, for CPU and RAM numbers). The first-launch download described above is a target; for now the model is pulled by hand.

```text
python -m app.main                       # run with real timers (away after 5 min idle)
python -m app.main --preview 15          # log windows for 15 s, then show the recap now
python -m app.main --demo                # real flow, "away" shortened to 10 s
python -m app.main --preview 25 --debug  # also print the raw model answer and why a line was replaced (terminal only, never saved)
python -m app.main --backend mock        # ollama (default) | mock | hailo (stub that falls back to Ollama)
python -m app.main --no-model            # rule-based lines only, no model call
python -m app.main --cpu-only            # ask Ollama not to use the GPU
python -m app.hud --sample               # show the recap window with sample text

python -m bench.run_bench --runs 30      # benchmark both fake scenarios (office, web_heavy)
python -m bench.run_bench --runs 10 --cpu-only
python -m bench.run_bench --runs 10 --no-model
python -m tools.vision_eval              # vision model check on a folder of screenshots (results are written outside the repo)

python -m unittest -v                    # run the 168 tests
```

Benchmark results are saved to `bench/results/<timestamp>/` (`results.csv`, `summary.md`); `bench/summary.md` is the latest summary. No window titles or model answers are written to any file.

## Limitations

- Tested on one laptop with English windows only.
- The UGen300 has not been used yet; its numbers come from the datasheet.
- A 1.5B model gives modest next-step suggestions. When recent windows are web pages, it tends to continue the main document rather than the last page.
- Vision descriptions can misread and still copy some names.
- GPU mode gave unusable vision output on this laptop.
- Items marked “Target” are not built yet.

## References

1. Mark, G., Gonzalez, V. M., Harris, J. “No Task Left Behind? Examining the Nature of Fragmented Work.” CHI 2005, pp. 321–330.
2. Microsoft. Copilot+ PCs. https://www.microsoft.com/en-gb/windows/copilot-plus-pcs
3. Screenpipe. “What is screenpipe?” https://screenpi.pe/about
4. ASUS. UGen300 USB AI Accelerator datasheet (UGen300-USB-8G); benchmark figures from Hailo.

## About

Built by 蘇靖飛 (GitHub: geoffreysugs) for the ASUS UGen AI League. License: not chosen yet.
