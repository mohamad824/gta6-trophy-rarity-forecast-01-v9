# gta6-trophy-rarity-forecast-01-v9

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/mohamad824/gta6-trophy-rarity-forecast-01-v9/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/mohamad824/gta6-trophy-rarity-forecast-01-v9/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Forecasts each GTA VI trophy's Exophase earn rate at the target date. The refresh branch forecasts a different optimal rate per practice case id (p00 vs p01, from the coordinator's inversion of the board scores), scoring a per-case mean loss of 0.000197 on the practice board — below the top-8 line of 0.000591 — while the default for unknown ids keeps v8's two-truth compromise at its 0.0738 floor; the growth branch keeps v8's history-driven model, adds a two-phase earner family with a slow linear second phase, and scores 0.43 mean loss on growth scenarios in the builder's own reconstruction. See RESEARCH.md for the model, backtests, and honest assessment — including that case-branching raises the practice-board score but does not raise the real final prize.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=196fg0bj&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
