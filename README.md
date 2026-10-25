# recallplug

## Usage

Requirements: Windows, Python 3.12 and [Ollama](https://ollama.com) running locally with the
model pulled (`ollama pull qwen2.5:1.5b`). `pip install -r requirements.txt` is optional
(psutil, for CPU/RAM numbers).

```powershell
python -m app.main                      # run with real timers (away after 5 min idle)
python -m app.main --preview 15         # log windows for 15 s, then show the recap now
python -m app.main --demo               # real flow, 'away' shortened to 10 s
python -m app.main --preview 25 --debug # also print the raw model answer and why a bullet
                                        # was replaced (terminal only, never saved)
python -m app.main --backend mock       # ollama (default) | mock | hailo (Stage II stub)
python -m app.main --no-model           # rule-based bullets only, no model call
python -m app.hud --sample              # show the HUD with sample text

python -m bench.run_bench --runs 10     # benchmark both fake scenarios (office, web_heavy)
python -m bench.run_bench --runs 10 --backend mock --scenario web_heavy
python -m bench.run_bench --runs 10 --show-rejections  # print why bullets were replaced
python -m bench.run_bench --runs 10 --no-model         # rule-only baseline
python -m bench.run_bench --runs 10 --ground-windows 2  # strict bullet 3 check (default 6)

python -m unittest -v                   # run the tests
```

Benchmark results are saved to `bench/results/<timestamp>/` (`results.csv`, `summary.md`);
`bench/summary.md` is the latest summary. Timing numbers from normal use go to
`bench/metrics.csv`. No window titles or model answers are written to any file.
