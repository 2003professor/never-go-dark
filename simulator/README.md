# KATIM Gateway 9001-R seamless-continuity simulator

Trace-driven, deterministic Python simulator of a KATIM Gateway 9001-R pair
(a moving team's gateway ↔ a command gateway) carrying mission traffic over
**real measured** radio bearers, used to test two learned, auditable policies
— **P1** freshness-aware scheduling and **P2** make-before-break — against a
faithful emulation of KATIM's **documented** design and the standard existing
mechanisms (IKEv2 MOBIKE, 3GPP ATSSS, always-warm) plus a perfect-foresight
oracle.

The contract is **`SPEC.md`** (frozen). This repository implements it. Only the
policy files change after the freeze; everything else — engine, gateway, traffic,
scenarios, metrics, baselines — is frozen and change-controlled (SPEC §10). The
shipped policy is **`policies/policy.py`** (the full Never Go Dark policy: P1
freshness + P2 predictor); `policies/continuity_policy.py` is a retained stub,
kept only for the frozen two-file contract and the isolation test.

> **The measured datasets are not bundled** (third-party licences — see
> [`../SOURCES.md`](../SOURCES.md)). The commands below assume the processed CSVs
> have been obtained and placed under `traces/processed/`; the frozen result JSONs
> already in `results/` are the outputs of those runs.

## One-command reproduction

```bash
# run every baseline on the TRAIN split -> results/bar.{csv,md} + figures
# (incl. the oracle emissions frontier and the per-run oracle lexicographic
#  check, SPEC §13.4/§13.8; the oracle is a foresight PLANNER over 20
#  seeds, so this is ~20 min)
python run_baselines.py            #  ~20 min, no network

# evaluate any single policy (frozen metrics + scalar score vs documented)
python -m eval.evaluate --split train   --policy policies/policy.py
python -m eval.evaluate --split heldout --policy policies/policy.py --seeds heldout

# fast unit tests
python tests/test_sim.py           #  (or: python -m pytest tests/ -q)

# §6.3 handover-event rule, validated on the UCC 5G NetworkMode switch
python -m predictor.events
```

`make reproduce` and `make test` wrap these. No network is needed at run time.
The measured datasets are **not bundled** (third-party licences — see
[`../SOURCES.md`](../SOURCES.md)): `make reproduce` needs them; `make test`'s
pure-logic invariants run without them (data-dependent tests skip cleanly).
Trace processing (raw → `traces/processed/`) is a separate `make traces` step: place the raw
downloads under `traces/raw/<dataset>/` first (parsers in `traces/parsers/`; see [`../REPRODUCE.md`](../REPRODUCE.md)).

## What it models (SPEC pointers)

| Piece | File | SPEC |
|---|---|---|
| 100 ms tick, zero-order-hold replay, validity horizons, gap→UNUSABLE | `sim/bearers.py`, `sim/engine.py` | §2, §3 |
| GEO measurement-parameterised model + seeded rain fade | `sim/bearers.py` | §3.6 |
| 8 QoS planes, documented timers, handshake cost, roam switch, tunnel state machine | `sim/gateway.py` | §4 |
| 15 responders + command; PLI / alert / voice / video / bulk; message semantics rule | `sim/traffic.py` | §5 |
| Scenario matrix S1–S6, stitched overlap, handover-event rule | `sim/scenarios.py` | §6, §3.7 |
| Policy interface (observable inputs only) + isolation gate (load-time static check, oracle-only instance binding) | `policies/interface.py`, `sim/isolation.py` | §7, §12.5 |
| Baselines: documented, MOBIKE, ATSSS, always-warm, oracle | `policies/*.py` | §9, §11 |
| Frozen metrics (AoI, alert gate, voice gate, outage, emissions, goodput) + scalar score + by-trace split | `eval/metrics.py`, `eval/evaluate.py` | §8, §6.4 |

### Bearers (all real)
The trace loader (`discover_traces`) picks up **every** processed dataset placed
under `traces/processed/` — UCC 5G, UCC 4G LTE, Starlink-on-the-Road (LEO),
Meireles V2I Wi-Fi, HSR-MPTCP (obtain these per `../SOURCES.md`) — and will pick
up `lumos5g` / `berlin_v2x` automatically if they appear, but never requires them. GEO is a
measurement-parameterised **model** (labelled as such everywhere), never a
replay.

### The policy sees only an `Obs`
Every policy is called each tick with a frozen `Obs` (per-plane queue depths and
head ages, link telemetry the gateway estimates from its own keepalives, red-side
message metadata, a coarse bearer hint, standby state) and returns an `Action`
(remap / drop / per-plane rate-cap / warm-standby / cutover / teardown / bounded
timer tweaks). It gets **no** handle to the engine, the trace arrays, the
transition schedule, future samples, the oracle, the RNG or the metrics (SPEC
§7.4). How that is enforced (SPEC §12.5, `sim/isolation.py`, frozen):

* **Load-time static gate.** `load_policy` checks a policy file *before*
  executing it and rejects imports outside `policies.interface` + pure stdlib
  helpers (no `sim`, `eval`, `pandas`, `numpy`, `os`, `sys`, `importlib`),
  file/code/introspection primitives (`open`, `exec`, `eval`, `getattr` ...),
  dunder and frame/traceback access, and any use of the oracle-only instance
  hook.
* **Oracle-only instance binding.** The scenario instance is bound only to the
  frozen `policies/oracle.py` class, identified by verified source file, not by
  a settable attribute. Any other policy that asks for it makes the run fail.
  Only frozen baselines may pick a gateway mechanism; editable policies run on
  the pessimistic default and act through the `Action` channel alone.
* **Negative unit tests** exercise each bypass the review probe used (a
  `wants_instance` policy, importing `sim.*`, reading a processed CSV with
  pandas, `open()`, dunder/frame escapes, a byte-identical copy of the oracle at
  another path, a policy selecting MOBIKE roaming) and assert they are refused.

This is a strong guard against accidental and loop-generated leaks. It is not
a security sandbox against a determined adversary (Python cannot be fully
sandboxed in-process); a hard guarantee would run the policy in a separate
process fed a serialised `Obs`. The oracle is the one explicitly privileged
exception (the ceiling) and any learned policy that beats it is quarantined.

## Results (TRAIN split) — see `results/bar.md`, `results/bar.csv`, `results/figures/`

Regenerated 2026-10-08 after the review fixes in SPEC §12 (all earlier numbers
are superseded). Mean over frozen train seeds `[0,1,2,3,4]` × 24 train scenario
instances (120 runs per mechanism). **Outage** (§8.4) = seconds no plane can
deliver = tunnel dark **plus** stall (tunnel up over <2.4 kbps deliverable
capacity); the emissions proxy is EMISSION transmit-seconds (§8.5/§14.2):
airtime on the non-primary/satcom leg whatever its role, so riding the standby
is never free; standby-tx is reported alongside.

| Mechanism | AoI p95 (s) | Outage / transition (s) | of which stall (s) | Standby-tx (s) | Satcom ovh (B) | Alert-hit |
|---|---|---|---|---|---|---|
| Documented reactive (emulated 9001-R) | 16.9 | 25.35 | 5.39 | 0.0 | 7983 | 0.739 |
| MOBIKE-style (RFC 4555) | 14.4 | 19.88 | 5.48 | 0.0 | 5609 | 0.772 |
| ATSSS-style (TS 23.501) | 11.5 | 16.31 | 2.46 | 34.5 | 10565 | 0.846 |
| Always-warm | 11.5 | 16.30 | 2.44 | 34.5 | 10565 | 0.846 |
| Oracle (perfect foresight) | 11.2 | 16.06 | 2.29 | 18.9 | 9595 | 0.850 |

What changed and why it matters:

* With saturating Download captures as the 5G capacity source, the 5G legs
  carry 5–80 Mbps median instead of ~0.3 Mbps. The earlier "ATSSS / always-warm
  / oracle rescue AoI from ~30 s to ~1.3 s on S3/S6" was an artefact of
  switching away from a video player's idle buffer, and it is gone.
* With GEO availability now meaning 94 % link-up (≈6 % down), every GEO
  scenario carries rain-fade outages **after** the transition, when there is no
  alternate bearer. Mechanisms whose post-transition recovery is the documented
  re-handshake (documented, ATSSS, always-warm, oracle) pay DPD + retry +
  handshake for each long fade; MOBIKE-style migration pays DPD + ~1 RTT. That
  is why MOBIKE beats ATSSS on S3 outage (10.2 s vs 12.5 s).
* No mechanism, the oracle included, passes the gates on S1, S2, S3 or S6. The
  computed breakdown in `bar.md` shows why. On GEO the voice median sits just
  above the 300 ms MCPTT limit (~320 ms). On S1–S4 and S6 the oracle's
  alert-hit is also below 0.99, because of real capacity stalls and rain-fade
  outages. GEO voice physics is not the only blocker.
* Outage/transition is driven by S2 (duty-cycled Starlink measurement gaps,
  ~65–75 s for all mechanisms). See the per-scenario tables.
* S-STALL sensitivity (stall floor 0 / 2.4 kbps / 50 kbps): overall
  outage/transition for documented = 24.50 / 25.35 / 27.63 s, oracle =
  15.21 / 16.06 / 18.27 s. The ordering is unchanged.

## Data honesty (real-data caveats, stated not hidden)

* **Zero-order hold, never interpolation.** A field is held from its last real
  sample only within its horizon; past the horizon it is absent and a
  throughput gap makes the bearer UNUSABLE for those ticks (modelled as an
  underlay outage, never filled). Unit-tested.
* **Capacity comes only from saturating captures.** UCC 5G throughput is
  application goodput. The Netflix/Amazon-Prime streaming captures log the
  player's *demand* (0 whenever the buffer is full), not link capacity, so they
  are **never** used as a capacity series. The 5G legs use the 21 file-Download
  captures only, starting once the transfer is flowing. Zeros during a running
  download (~10 % of `State=D` rows) are kept as genuine stalls and count as
  outage. Feeding a demand series to every mechanism would **not** have been
  neutral, because stall-triggered switchers react to the artificial stalls.
  The earlier claim that it "cannot flatter the policy" is retracted (SPEC §12.1).
* **Remaining app-limited sources.** UCC 4G (S4's cellular leg) logs ~8 % idle
  (`State=I`) zero rows that cannot be told apart in the unified schema. HSR-MPTCP
  per-subflow throughput depends on the MPTCP scheduler, so a zero window on one
  subflow may be the scheduler favouring the other one. Both now count as outage
  under §8.4. Treat absolute S4/S5 outage numbers with that caveat.
* **Starlink-on-the-Road is duty-cycled** (~15 s iperf bursts / ~55 s), so its
  between-burst measurement gaps are modelled conservatively as outage for all
  mechanisms equally (S2); S3/S6 (GEO model, continuous) and S4 (Wi-Fi cliff)
  are the cleaner differentiators.
* **GEO is a model, not a replay.** It uses FCC MBA + IIUM SES-12 parameters
  with a seeded rain-fade episode process. 94 % availability means the modelled
  link is down ≈6 % of the time (unit-tested). Every figure caption and results
  table says so, and says which scenarios are stitched from independently
  measured traces.

## Layout

```
SPEC.md                       frozen contract
sim/bearers.py                replay (ZOH + horizons), GEO model, discovery, split
sim/gateway.py                8 planes, timers, handshake cost, roam, tunnel FSM
sim/traffic.py                PLI/alert/voice/video/bulk, 15+1, semantics rule
sim/scenarios.py              S1–S6 matrix, stitching, §6.3 event rule
sim/engine.py                 100 ms tick loop, committed-bearer tracking, delivery, rate caps, outage=dark+stall
sim/isolation.py              SPEC §7.4 enforcement: load-time gate + oracle-only instance binding
policies/interface.py         Obs/Action dataclasses (observable inputs only)
policies/static_documented.py MOBIKE / ATSSS / always_warm / oracle baselines
policies/policy.py            the shipped Never Go Dark policy (P1 + P2 + predictor)
policies/continuity_policy.py retained stub (frozen two-file contract + isolation test)
eval/metrics.py               frozen metric definitions (§8)
eval/evaluate.py              split, scalar score, one-command CLI
predictor/events.py           §6.3 event rule + UCC-switch validation
run_baselines.py              one command -> bar.{csv,md} + figures
figures.py                    the three headline figures
tests/test_sim.py             ZOH, gap→unusable, handshake, outage(+stall), isolation negatives, rate caps, GEO availability, split
results/                      bar.csv, bar.md, figures/
```
