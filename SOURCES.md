# Sources

## Measured datasets (not redistributed here — obtain from the originals)

We use **real measured data only**; where no real trace exists we parameterise from published measurements and say so. The raw datasets are
third-party and are **not included in this repository** — each is available from its own source under its own licence. Full vetting notes are
in [`reference/TRACES.md`](reference/TRACES.md).

| Dataset | Reference | Source | Licence |
|---|---|---|---|
| UCC/MISL 5G | Raca et al., *Beyond Throughput: The Next Generation*, ACM MMSys 2020 | github.com/uccmisl/5Gdataset | GPL-3.0 |
| UCC/MISL 4G LTE | Raca et al., *Beyond Throughput: a 4G LTE dataset…*, ACM MMSys 2018 | zenodo.org/record/1219679 | CC BY 4.0 |
| Starlink on the Road | Laniewski et al., TMA 2024 (arXiv:2403.13497) | github.com/sys-uos/Starlink-on-the-Road | CC BY-SA 4.0 |
| Lumos5G (mmWave) | Narayanan et al., ACM IMC 2020 | ieee-dataport.org/node/3598 | CC BY 4.0 |
| HSR-MPTCP | Li et al., ACM SIGCOMM 2018 | github.com/superlitong/hsr-mptcp | MIT |
| Meireles V2I Wi-Fi | Meireles et al. | zenodo.org/records/6884095 | CC BY 4.0 |
| Berlin V2X | Hernangómez et al., IEEE VTC 2023-Spring | DOI 10.21227/8cj7-q373 | per dataset terms |

**GEO satellite** has no open per-second throughput trace, so the GEO bearer is a **measurement-parameterised model** (levels anchored on
FCC *Measuring Broadband America*, satellite subset; rain-fade / RTT physics from published measurements). It is labelled **MODEL**, never
"replayed". Scenarios that combine independently-measured legs are labelled **STITCHED**; the HSR rail capture is **REAL SIMULTANEOUS**.

## From raw datasets to processed traces

Each raw dataset is converted to **one processed trace per run**, in a single unified schema (`t_s, bearer, dl_mbps, ul_mbps, rtt_ms, loss`;
monotonic time base; one row per real sample; never interpolated — zero-order hold). Every processed file maps 1:1 to exactly one raw run
(provenance). The schema and the per-dataset column mapping are specified in [`simulator/SPEC.md`](simulator/SPEC.md) §3.3–3.8; the simulator
discovers processed traces under `traces/processed/<dataset>/`. The dataset-specific parsers are included in
[`simulator/traces/parsers/`](simulator/traces/parsers/): place the raw downloads under `simulator/traces/raw/<dataset>/` and run `make traces`
(from `simulator/`) to build the processed traces — see [`REPRODUCE.md`](REPRODUCE.md). The raw data itself is not redistributed here. The
frozen evaluation outputs are in [`results/`](results/).

## KATIM / EDGE public documentation (referenced, not reproduced)

The documented 9001-R design we adapt is derived from EDGE's **public** KATIM materials (product datasheets, the gateway family datasheet,
and the network-security white paper). Every KATIM claim in the project is tagged **[D] documented** or **[I] inferred** in
[`reference/ARCHITECTURE-MAP.md`](reference/ARCHITECTURE-MAP.md). No EDGE/KATIM logos, trademarks, or copyrighted images are used.

## Affiliation

This is independent work built from public documents. It is **not affiliated with, sponsored by, or endorsed by EDGE** or KATIM.

## AI assistance

AI tools (Claude) aided in the research and design of this project.
