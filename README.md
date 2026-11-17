# gta6-twitch-ccu-forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/oatech-ignacio/gta6-twitch-ccu-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/oatech-ignacio/gta6-twitch-ccu-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

An analog forecaster for GTA VI's launch-day Twitch viewers: it turns every past launch in the case into an hour-by-hour curve relative to its first hour, weights those launches by how much they resemble the case at the checkpoint (unlock pattern, platforms, first-hour size and trend), and writes the weighted median curve with an empirically calibrated, asymmetric quantile spread for hours 2 to 24.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=jvyyasxq&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>

## How it works

- **Curves.** Each comparable launch becomes `ln(viewers in hour h / viewers in hour 1)` for hours 2..24 of its UTC release day.
- **Weights.** A prior from the launch's notes (rolling midnight-local unlock and console-only up; early-access-contaminated, single global unlock and weak tiers down) times a Gaussian kernel on three things known at the checkpoint: the size of hour 1, hour 1 relative to the previous 24 hours' peak (is the launch already live?), and the trend into hour 1.
- **Size correction.** Bigger launches grow less over the day, and a kernel average at the top of the size range leans on smaller neighbours. Per hour, each neighbour's log-ratio is moved along half of the weighted least-squares slope in `ln(hour-1 viewers)` to the case's own size, clipped to the neighbours' size range so nothing is extrapolated past the data.
- **Median.** Weighted median of the corrected log-ratios per hour, lightly smoothed across neighbouring hours.
- **Spread.** Weighted 25/75 quantiles give the inner pair; a blend of 25/75 and 10/90, scaled by 1.1, gives the 5/95 tails; lower and upper sides are separate, with a random-walk floor of `0.14 * sqrt(h - 1)`.
- **Momentum.** When minute reads exist (the final case), the level in the last 15 minutes of hour 1 relative to the hour's mean nudges the first forecast hours, decaying over about three hours. Practice cases have hourly points only, so this is inactive there and is not backtested.
- Standard library only, deterministic, about 0.1 s for the four practice cases; falls back to the house baseline if a case cannot be parsed.

## Backtest

`python3 backtest.py` runs a leave-one-out test: each of the 37 comparable launches in the kit is forecast from its own first hour using only the other launches, scored with the contest's loss (mean pinball loss on the log scale).

| Set | Model | House baseline | Model 90% coverage |
| --- | --- | --- | --- |
| All 37 launches | 0.4427 | 0.9297 | 0.84 |
| 26 launches already live in hour 1 (>= 20k viewers, not weak tier) | 0.2569 | 0.3257 | 0.90 |

The second row is the one closest to the final case. The parameters were chosen coarsely on this same backtest, so the numbers are somewhat optimistic. The four practice cases cannot be scored locally (the kit does not hold their answers); the script only checks that their forecasts are valid.

Tried and dropped, because the backtest did not support them:

- A kernel feature for how long the launch had already been live before the checkpoint (consecutive hours at half of hour 1's level or more): worse at every bandwidth (0.2638 to 0.2887 on the second row, against 0.2632 without it).
- Shifting comparables by one hour to match US daylight-saving time (the US-east midnight jump sits at 04:00Z in summer launches and 05:00Z in winter ones): slightly worse overall (0.2687 against 0.2632) and in hours 4 to 8.

Known limits: the median still runs a little high late in the day on the second row (about 61% of answers fall below it), and when hour 1 is still rising before the US unlock the median shows no jump at 05:00Z; that case is covered by the upper quantiles only.
