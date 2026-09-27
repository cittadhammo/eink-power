# eink-power — how much is the e-paper screen costing this laptop?

A quest log and a measuring instrument. Started 2026-09-26 on `framework13`
(Framework Laptop 13, AMD Ryzen 5 7640U, mainboard `FRANMDCP05`, Omarchy,
kernel 7.2.5-3-omarchy).

**The one number, on its own page: <https://www.dhammacharts.org/eink-power/>**
(that is this repo's GitHub Page — the account's user site holds the
`dhammacharts.org` domain, so the project page is served from that host).

## The question people keep asking

> "Is the e-ink screen actually saving you battery life?"

Short answer: **about 1.4 W**, measured on this machine, and that is cheaper
than a bright laptop backlight but in the same league as a dimmed one — so not
a battery revolution, but not the disappointment the price tag suggests
either. The interesting part is *why*, and the catch is that it only holds
while the image is still.

**If you only want one number**, it is what the monitor costs while in use,
and it is four minutes of work:

```sh
cd ~/Github/eink-power
systemd-inhibit --what=idle:sleep --why="eink power test" ./bin/einkpower.py unplug-test
```

The kit takes video *and* power through one USB-C cable, so pulling that
cable removes the entire e-paper subsystem at once. The drop in battery watts
between the plugged and unplugged windows is the display's cost, full stop.

**Measured, 2026-09-27: the monitor costs about 1.4 W.**

## What the screen actually is

Not a panel. A *kit*. The EDID on `DP-3` says `Paper Monitor`, and the
hardware behind it is a **Modos Paper Dev Kit, 13-inch version**
(`Modos-Labs/Glider`):

| part | detail | power relevance |
| --- | --- | --- |
| panel | E Ink Carta 1000, 13.3", 1600x1200, **no frontlight** | ~0 W while holding an image; large short-lived spikes when driven |
| controller | Xilinx Spartan-6 LX16 FPGA, open-source `Caster` gateware | always-on silicon, the dominant fixed cost |
| framebuffer | DDR3-800 on-board | always-on |
| MCU | STM32H750 (USB comms, firmware) | small |
| panel PSU | ±15 V rails, **rated 1 A peak** | Modos notes modern high-res panels can exceed **20 W peak** |
| link | DisplayPort 1.2 / HDMI (DVI) in, over **one USB-C cable that also carries power** | see below |

Two consequences that decide everything:

1. **The kit is powered by the laptop.** The single USB-C cable does DP-Alt
   for video *and* PD for power. So the e-paper subsystem's watts come out of
   the same battery as the laptop. Nothing about it is "off the books" — which
   also means it is measurable, at least in aggregate.
2. **The saving is in the duty cycle, not the hardware.** E-paper consumes
   power to *move ink* and almost none to *hold ink*. Freeze the screen and
   the panel sleeps; scroll continuously and it is being driven nonstop, at
   which point the e-ink advantage is gone while you still pay for the FPGA,
   the DDR3 and a GPU running the compositor at 75 Hz.

## What this machine can and cannot tell you

Run `./bin/einkpower.py doctor` to regenerate this on your box. Saved output:
`results/doctor.txt`.

**Cannot — the panel is a black box.** No panel power, current, refresh count,
VCOM waveform, temperature or per-region activity is exposed anywhere. There
is no kernel driver for it, no ACPI device, no I2C endpoint, no USB telemetry
channel. To the OS it is a plain DP sink on `drm_dp_aux3`; Hyprland sees
`1600x1200@75Hz, XRGB8888, sdrBrightness 1` and stops there. The FPGA decides
on its own what to refresh and when, and tells nobody.

**Can — the difference between two states.**

| signal | source | notes |
| --- | --- | --- |
| whole-machine power | `hwmon2 (BAT1) in0_input x curr1_input` | **the number to use.** 1 Hz, no root, 1 mA / 1 mV resolution. Identical to `power_supply/BAT1/{voltage,current}_now` |
| SoC package power | `hwmon4 (amdgpu) power1_input`, label `PPT` | the SMU's own board-power figure. Separates "the chip" from "everything else" |
| GPU engine busy | `/proc/*/fdinfo/*` `drm-engine-gfx` | per DRM client, cumulative; deltas give % of wall time. Proxy for how hard the display pipeline is working |
| CPU busy | `/proc/stat` deltas | needed to prove two runs are comparable |
| display DPMS state | `/sys/class/drm/card1-DP-3/dpms` | for the on/off states |
| RAPL domains | `/sys/class/powercap/intel-rapl:*` | **root-only** (`energy_uj` is 0400) and AMD's counters are indicative, not calibrated |
| net traffic | `/proc/net/dev` deltas | to catch a stray download skewing a run |

**Traps I fell into, so you do not have to:**

- `upower` is installed and looks like the obvious tool. Its `energy-rate` is
  heavily smoothed: it sat at `10.062 W` while the pack was actually pushing
  `10.46 W`, and earlier at `7.89 W` when the pack was at `10.8 W`. It is a
  30-second-to-minute average that also lags. Do not sample it in a test.
- The per-USB-C-port numbers look like a gift for this question: ports
  `USBC000:003` and `:004` report a negotiated `5.000 V / 1.500 A`. They never
  moved once during a 4-minute log, and they read as a *contract*, not a
  *draw*. Do not quote them as wattage. (This matters a lot here — see
  "what the battery should look like" below.)
- `powertop` is in `extra` and looks like the obvious install. Its display
  model is a **backlight** model, and `/sys/class/backlight` on this machine
  is *empty* (correctly: no frontlight, no backlight). Whatever it prints
  under "Display backlight" is an extrapolation, not a measurement. It also
  needs root for the RAPL part.
- Framework's own tools (`framework-tool`, BMC telemetry) do not exist on the
  Laptop 13 mainboard. There is no BMC.

## The measurement problem, and why the design looks like this

Whole-machine draw on this box moves a lot on its own. During today's
baselines, with a nominally constant workload, total draw ranged
**10.0 W to 14.2 W (sd 1.29 W over 4.5 min)**, and two nominally identical
runs 20 minutes apart differed by 1.4 W. Wi-Fi, the agent process doing this
analysis, thermals and compositor activity all move it.

That noise band is the same size as the effect being measured. So the naive
"5 minutes with the screen on, 5 minutes with it off" design cannot work. The
tool therefore does **paired, interleaved blocks**: alternate A and B in short
blocks, compute a difference per round, and report the spread of those
differences. Drift then hits both states equally instead of masquerading as a
result. This is the whole reason `ab-test` exists.

It was checked against a null control: two blocks with the *same* command in
both arms, on this machine, gave `-0.14 ± 0.21 W` over two rounds — no false
signal. Also note how the total drifted from 12.5 W to 11.2 W over a
three-minute smoke run: unpaired long blocks would have reported that drift
as a result.

## How to run the tests

Nothing to install, no root, no packages: Python 3 and a terminal.

```sh
cd ~/Github/eink-power

./bin/einkpower.py doctor                    # what this box exposes
./bin/einkpower.py verify                    # trust check on the sensors (5 min)
./bin/einkpower.py log --duration 300        # raw 1 Hz CSV into results/
./bin/einkpower.py report                    # summary table of every log
```

`verify` is worth running once before trusting anything: it integrates the
current sensor over time and compares that against what the battery's own
coulomb counter (`charge_now`) says happened. If those two disagree, one of
them is lying and every wattage figure here is fiction. On this machine they
agree to within 1% over 66 s and 272 s windows, so the readings are sound.
Give it 300 s: `charge_now` has only 1 mAh resolution, and a short window is
all quantisation.

### 1. The decisive test: pull the cable

The single USB-C cable carries both video and power, so unplugging it removes
the *entire* e-paper subsystem — panel, FPGA, DDR3, panel PSU and the GPU's
display pipeline — in one action. Whatever the battery draw drops by is the
real total cost of the display.

```sh
systemd-inhibit --what=idle:sleep --why="eink power test" \
  ./bin/einkpower.py unplug-test     # 3 phases, ~4 min, prompts at each switch
```

For an error bar instead of a single number, interleave the switches:

```sh
systemd-inhibit --what=idle:sleep --why="eink power test" \
  ./bin/einkpower.py ab-test --manual --block 30 --rounds 4
```

It will ask you to unplug and replug every 30 s, eight times. Tedious, and it
is the difference between "about 7 watts" and "7.1 ± 0.2 W".

**You will hear two bells when each window closes** — that is the signal to
plug back in. The panel holds its last frame while unplugged, so sound is the
only way to tell you the window ended. (If your terminal has no bell, keep a
phone timer as backup and treat the blind window as 90 s.)

**Running it blind is fine, and here is why.** This panel has no frontlight,
so with the cable out the e-paper holds its last frame — the terminal text
stays on the screen, readable, while the machine is measurably display-less.
You are never actually blind; you just cannot see *new* output until you plug
back in. The tool timestamps every block itself, so your reaction time does
not corrupt the boundaries: a block is a fixed number of samples taken by the
tool, and only the switch instant inside it is yours.

**Rules that keep the number honest:**

* **stay on battery.** Unplugged AC, everything. On AC the pack current stops
  being system power and the whole measurement is meaningless.
* **lid open, no idle, no screensaver** — hence `systemd-inhibit` above.
* **do not touch mouse or keyboard inside a window.** Any repaint is panel
  refresh work, which is exactly the thing being measured. Between windows,
  feel free to move.
* **one cable, one monitor.** See below.

### What to do with your apps during the test

Keep them open. A static on-screen image is what makes the plugged arm
meaningful; an empty desktop would under-measure what you actually do with
this machine. The rule is not "few apps", it is:

> **same apps in both arms, and not repainting.**

* **Static content is fine and desirable** — a browser on a text page, a
  terminal, a file manager at rest. Those are exactly the workloads e-paper
  is good at, and they are the ones worth measuring.
* **Never repainting is the requirement.** Video, animated GIFs, live pages
  with moving ads, a `top` in a loop, a spinner, a chat app with an animated
  presence indicator — all of these refresh the panel, and the refresh is
  real power. Kill them before you start, not mid-test.
* **Change nothing between the windows.** No new tabs, no closing windows, no
  downloads starting. A state change that lands in one arm only reads as a
  result.
* **Network-hungry apps add noise** to the whole-machine figure. A paused
  local page is better than a live one.
* **A blinking text cursor is negligible** and identical in both arms, so
  ignore it. (There is no runtime toggle for `cursor:no_blink` in this
  Hyprland version, and it is not worth editing your config for.)
* **Let the agent be idle.** If this test is run while I am working, my own
  tool calls add CPU load and screen repaints. Ask me to go quiet first.

Note the asymmetry that makes this the *right* number: while the monitor is
unplugged, none of that repainting costs any panel power, because there is no
panel. So the delta is not "the panel in a vacuum" — it is what using this
display actually costs you in practice, which is the figure people are asking
for.


### Why not add a second screen to stay connected

It would invalidate the experiment. A second display brings its own 2–4 W
(Framework's own LCD is ~3.4 W max panel power) *and* changes what you are
measuring: the compositor would then render to two outputs, the GPU display
pipeline would stay awake in the "e-ink removed" arm too, and the delta would
be "e-ink minus LCD" instead of "the e-ink system's cost". Also, with no
internal panel on this machine (`card1-eDP-1` is absent), a second monitor
takes the port and the configuration you have at test time is the
configuration you must keep.

If staying connected during the measurement matters more than the number, buy
the $10–25 USB-C power meter instead (test 4). It is less rigorous
methodologically but it is zero-risk, needs no blind windows, and reads the
panel's watts directly.

### 2. What does the display cost while asleep?

```sh
./bin/einkpower.py ab-test --block 30 --rounds 4     # toggles DPMS itself
```

e-ink keeps its last image, so nothing visually disappears. This measures the
floor: panel holding, FPGA idling, link powered.

Note the blind spot: DPMS-off measures the *sum* of panel + board + link +
display engine. Separating those three needs either a power meter in the
cable (test 4) or a second host to borrow the panel from.

### 3. Does the update rate matter? (the physically important one)

```sh
./bin/einkpower.py experiment            # idle / typing / scrolling / dpms-off
```

Do the scrolling phase honestly — a long page, or `yes` piped into a
fullscreen pager — because a static screen is the best case for e-paper and
an animating one is the worst. The gap between those two rows is the real
story of e-ink on a laptop.

`ab-test` is scriptable, so any two machine states can be A/B'd, not just
DPMS. The refresh rate is the obvious second one, and it is the lever the
whole e-paper trade-off turns on:

```sh
./bin/einkpower.py ab-test --rounds 4 --block 30 \
  --cmd-on  'hyprctl keyword monitor DP-3,1600x1200,75' \
  --cmd-off 'hyprctl keyword monitor DP-3,1600x1200,40' \
  --name-on A-75Hz --name-off B-40Hz --tag refreshrate
```

(If 40 Hz is not in the panel's `availableModes`, it will refuse; the EDID
advertises 75 Hz as the only mode here, so this one may be a dead end on a
stock kit — it is more useful on a Modos Flow, which ships 60 Hz panels.)

### 4. Ground truth: a USB-C power meter
The only way to read the panel's own watts without inferring them is to put a
meter in the monitor's power path. A USB-C PD power meter dongle (male→female,
with a live watt readout, ~$10–25, search "USB C power meter 100W") goes
between the laptop's port and the kit's cable. Read it during:

* panel idle, screen static → the fixed cost of FPGA + DDR3 + panel holding
* continuous scrolling → the worst case
* kit unplugged / board asleep → the true sleep floor

That single device answers the question without any of the statistics above,
and it is the recommendation if you only want to settle this once.

### 5. Optional: does the board sleep with the host?

Worth a look because it is the one failure mode that would cost you battery
while you are not even using the laptop. Cheap check: suspend the machine with
the monitor attached and watch the board. If its status LED goes out, the
rails are almost certainly down and there is nothing to measure. If it stays
lit, the board is drawing from the battery all night and test this over two
nights:

1. tonight, monitor **plugged in**, lid closed, note `charge_now` before
2. tomorrow: `journalctl --list-boots | head -3` to confirm it suspended, then
   read `charge_now` again
3. repeat with the monitor **unplugged**

Two nights, one number each.

## Results

### What the display costs: ~1.4 W

One `unplug-test` run, 2026-09-27, machine idle, static image on a Helium
text page, on battery, 1 Hz sampling, 45 s settle before each phase is
counted:

| phase | draw | vs connected |
|---|---|---|
| monitor connected | **6.66 W** (sd 0.04) | — |
| monitor unplugged | **5.22 W** | **-1.44 W** |
| monitor plugged back in | **6.75 W** (sd 0.09) | +0.09 W |

**The display costs about 1.4 W.** Three estimators agree: 1.44 W (settled
mean), 1.38 W (median), 1.50 W (quietest 5% of each phase). Bootstrap 95% CI
on the raw window means is 0.96–1.21 W, and the settled figure sits at the top
of that range because the transient drags the raw mean down.

What makes this more than one lucky reading: the third phase plugged the
monitor back in and the draw returned to within 0.09 W of where it started. A
drift or a coincidence would not come back. The connected phase held
sd 0.04 W over 65 s, so the machine was quiet enough to resolve 1.4 W at all.

In practice, on a 60.6 Wh pack:

- the monitor burns **~2.4% of the battery per hour** to hold a static image
- whole-machine runtime goes **9.1 h → 11.6 h**, so it costs about **21% of
  your available working time**

### A methodology trap worth knowing about

Unplug the cable and the draw does **not** step down. It decays smoothly from
~6.9 W to ~5.1 W over roughly a minute — the machine relaxes into the new power
profile rather than switching:

```
unplugged: 6.74 6.89 6.89 6.89 6.82 7.30 7.39 7.26 7.04 6.95 6.86 6.76
           6.67 6.60 6.53 6.48 6.41 6.32 6.26 6.12 6.05 6.01 5.95 5.89
           5.84 5.78 5.72 5.66 5.60 5.58 5.54 5.54 5.51 5.48 5.45 5.43
           ...                                   settling at ~5.10
```

A naive mean over the window reports **-1.09 W** and understates the effect by
about a third; a 30 s window would have been badly wrong. The unplugged phase
has to be long enough to reach its floor. `report --settle N` drops the first
N seconds of every phase, and `unplug-test` does it for you. If you rerun this,
lengthen the unplugged phase rather than shortening it to save time.

### The comparison that actually matters

For calibration, a stock Framework Laptop 13 with the original LCD is quoted
by the community at around **1.8 W with the backlight at 0%** and **4.3 W at
100%** (different OS and tuning, so order-of-magnitude, not a measurement of
this machine).

So the honest summary, and it is not the boring one: **1.4 W for a 13" Carta
1000 panel is cheaper than an LCD at any brightness you would actually read
it at.** The e-paper is not a battery-saving display by some margin — 21% of
runtime is not nothing — but it is in the same league as a dimmed backlight,
and well ahead of a bright one. Note also that those LCD figures include the
backlight, whereas the 1.4 W includes the FPGA, the board, and the whole GPU
display pipeline for a 1600x1200@75Hz stream.

The catch is what the 1.4 W is *for*: holding a static image, which is the
e-paper's best case and roughly a third of what an LCD costs while lit. On a
reader workflow — long static documents, occasional page turns — that is a
genuine win. On a scrolling one, the refresh cost is what would settle the
argument, and it is test 3.

### Earlier runs, and the battery itself

The first two logs, both with this analysis session as the workload, so a
*loaded* baseline rather than an idle figure:

| run | minutes | mean W | median | p05 | p95 | sd | cpu% | gfx% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `20260926-102039_validation-session-active` | 1.1 | 10.76 | 10.74 | 10.48 | 11.04 | 0.15 | 12.1 | 3.6 |
| `20260926-103222_baseline-session-quiet` | 4.5 | 12.12 | 12.41 | 10.01 | 14.00 | 1.29 | 15.2 | 2.3 |

The 4.5-minute baseline spanning 10.0–14.2 W is why the test design is
interleaved and paired: on a busy machine a difference of 1 W is a coin flip.
The idle unplug test above had sd 0.04 W, a completely different situation.

Battery: 60.6 Wh design (`charge_full_design` x `voltage_min_design`; upower
agrees), 9 cycles. `amdgpu` PPT ranged 4.8–10.7 W inside the 10–13 W loaded
total, so the SoC is most — but not all — of it.

Over 2026-09-26 11:30 → 2026-09-27 09:35 the pack went 3.19 Ah → 0.92 Ah,
35 Wh in 21.8 h. The journal shows four boots in that window and the machine
was in use, so that is a working day, not a mystery drain. `verify` confirms
the current sensor and the coulomb counter agree, so the wattage figures
themselves are sound.

## What this does not say

- **Nothing about refresh.** The panel is bistable: once the image is on the
  glass it stays there with no current at all. The 1.4 W above is the Glider
  board plus the FPGA sitting idle, holding a picture. A full repaint drives
  the panel drivers hard for seconds and costs more energy than holding does.
  This test was deliberately run with no repainting.
- **Nothing about the competition, measured on this machine.** The LCD figures
  above are quoted from the community, not measured here. A real head-to-head
  would switch the built-in panel and the Modos on and off the same static
  page, same session, same morning.
- **Nothing about suspend**, beyond the LED going out. See test 5.

## Files

```
bin/einkpower.py     the instrument (doctor / log / report / delta /
                     unplug-test / ab-test / paired / experiment)
results/             raw 1 Hz CSVs and the doctor snapshot
notes/sources.md     where the hardware facts come from
```

Nothing in this directory modifies the machine. It reads `/sys` and `/proc`
only. If you want the tool on your `PATH`, symlink it rather than copying:
`ln -s ~/Github/eink-power/bin/einkpower.py ~/.local/bin/einkpower`.

## Open questions

* How much of the display's cost is the panel versus the Glider board versus
  the GPU's display engine? `unplug-test` cannot split these; the power meter
  can (panel+board vs the rest).
* Does the 41 Hz panel variant (some Modos 13" kits ship 41 Hz instead of
  75 Hz) change the bill materially? It should, roughly linearly in refresh
  duty cycle.
* Whether the frontlight-free Carta 1000 panel's `sdrMaxLuminance 80` in the
  EDID has any real electrical meaning or is just firmware boilerplate.
