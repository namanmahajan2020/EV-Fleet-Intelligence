# Battery and range method evaluation

## Current method

The running system currently uses explicit threshold rules for battery health and the simulator-provided range estimate. `services/stream_processor/app/domain.py` labels battery health from reported SoH, temperature, and battery diagnostic codes. It reports reasons alongside the category. This is a transparent operational signal, not a prediction of remaining useful life.

`estimate_range_km(soc_pct, usable_capacity_kwh, consumption_kwh_per_100km)` provides a reproducible energy-balance baseline. The live dashboard currently displays the simulator's `range_km`; the helper is covered by a unit test but is not yet substituted for the simulator's value.

## Evaluation status

No independent labeled battery-aging or real-world range dataset is included. The deterministic simulator is not an independent ground-truth source, so a train/test score on it would measure agreement with assumptions encoded in the generator rather than real fleet performance. No scikit-learn model, held-out evaluation, accuracy claim, or saved model artifact is present. Metrics: **Not yet measured**. Current method version: `rules-v1`.

Before a learned model is considered, collect permitted labeled history, split by vehicle and time to prevent leakage, compare against the energy-balance and threshold baselines, report MAE/RMSE for range and a calibration/recall metric for health risk, then save the dataset lineage and model version with the evaluation.
