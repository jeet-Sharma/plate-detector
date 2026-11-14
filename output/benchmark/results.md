## Accuracy, time and memory

| Clip | Preset | Plates found | Precision | Character accuracy | Wall time | Real-time factor | Peak RAM (self) | Peak RAM (outside) | Peak private |
|---|---|---|---|---|---|---|---|---|---|
| 7377 | balanced | 89% | 100% | 89% | 77.6 s | 1.28x | 347.1 MB | 348.4 MB | 323.5 MB |
| 7378 | balanced | 100% | 100% | 100% | 60.5 s | 0.99x | 363.6 MB | 367.8 MB | 342.0 MB |
| 7377 | fast | 67% | 75% | 86% | 56.9 s | 0.93x | 340.0 MB | 343.0 MB | 316.9 MB |
| 7378 | fast | 100% | 100% | 100% | 53.0 s | 0.87x | 341.9 MB | 342.7 MB | 318.9 MB |
| 7377 | low-mem | 44% | 100% | 44% | 146.4 s | 2.43x | 253.4 MB | 256.6 MB | 230.5 MB |
| 7378 | low-mem | 80% | 100% | 79% | 122.4 s | 2.02x | 253.1 MB | 254.0 MB | 230.7 MB |

## Latency per call, in milliseconds (mean / 95th percentile)

| Component | 7377 balanced | 7378 balanced | 7377 fast | 7378 fast | 7377 low-mem | 7378 low-mem |
|---|---|---|---|---|---|---|
| Decode, per source frame (30 fps video) | 39.6 / 127.4 | 27.8 / 97.9 | 30.5 / 77.0 | 28.5 / 69.9 | 80.5 / 131.6 | 67.2 / 103.3 |
| Change gate | 0.3 / 0.7 | 0.3 / 0.4 | 0.2 / 0.4 | 0.2 / 0.4 | 0.4 / 0.8 | 0.3 / 0.5 |
| Convert to full-resolution image | 3.4 / 5.4 | 3.1 / 4.3 | 3.0 / 3.9 | 3.1 / 4.2 | 3.0 / 4.5 | 2.8 / 3.1 |
| Detector, single pass | 131.8 / 246.1 | 109.0 / 120.3 | 106.9 / 119.1 | 113.1 / 126.0 | 186.0 / 232.0 | 176.4 / 196.0 |
| Detector, 3-tile pass | 365.1 / 683.7 | 300.4 / 330.2 | 291.7 / 316.5 | 310.9 / 331.8 | - | - |
| Crop | 0.0 / 0.1 | 0.2 / 1.1 | 0.0 / 0.1 | 0.1 / 0.4 | 0.0 / 0.1 | 0.1 / 0.1 |
| OCR (all plates in the frame) | 22.6 / 132.1 | 88.2 / 182.4 | 9.2 / 62.7 | 44.4 / 100.5 | 15.4 / 112.0 | 108.0 / 286.8 |
| Tracker update | 0.1 / 0.3 | 0.2 / 0.8 | 0.1 / 0.2 | 0.2 / 0.7 | 0.0 / 0.1 | 0.1 / 0.5 |
| Whole frame through the models | 238.7 / 520.9 | 267.6 / 493.8 | 167.8 / 354.8 | 213.8 / 426.1 | 206.2 / 308.4 | 289.2 / 463.4 |
