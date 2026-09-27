# Sources

Everything factual in the README that is not from this machine's own sysfs.
Fetched 2026-09-26.

## The display hardware

- **Modos Paper Dev Kit, 13-inch** — Crowd Supply campaign page. Panel
  E Ink Carta 1000 (ED133UT3), 13.3", 1600x1200, up to 75 Hz, no frontlight,
  no touch, $599. Controller: Xilinx Spartan-6 LX16 running Caster,
  DDR3-800 framebuffer, STM32H750, "E-paper power supply with up to 1 A peak
  current on the +/-15 V rail". HDMI + USB-C DP-Alt.
  https://www.crowdsupply.com/modos-tech/modos-paper-monitor/
- **Glider hardware repo** (`Modos-Labs/Glider`) — same board, hardware
  design; confirms Spartan-6 LX16, DDR3-800, PTN3460 DP bridge / ADV7611 DVI
  decoder, ±15 V e-paper supply at 1 A peak, STM32H750.
  https://github.com/Modos-Labs/Glider
- **Modos blog, original Paper Monitor (2022)** — the clearest published
  power figure for the 13.3" 1600x1200 concept: "MicroUSB power input
  (consumes **1.5W~2W continuously**)". Note this is the earlier, smaller
  controller; the Dev Kit's Glider board is a much bigger FPGA with DDR3.
  https://www.modos.tech/blog/modos-paper-monitor
- **"A Technical Deep Dive Into Glider"** (Crowd Supply update) — the two
  quotes that matter: e-paper is "quite power hungry when it comes to peak
  current… modern high-resolution panels can consume **>20 W peak**", and the
  board carries voltage/current monitoring specifically to protect the panel.
  Also explains the Spartan-6 choice partly on leakage grounds.
  https://www.crowdsupply.com/modos-tech/modos-paper-monitor/updates/a-technical-deep-dive-into-glider
- **Modos Flow (next-gen controller)** — requires ≥7.5 W over USB-C today;
  Modos says the controller+panel combo is being optimised to under 5 W for
  single-cable use, and a lower-resolution panel would bring it to ~3 W.
  https://news.lavx.hu/article/hands-on-with-modos-tech-13-3-inch-e-paper-monitors-we-tried-the-current-dev-kit-model-and-the-next-gen-modos-flow-touch
- **Electrotrio hands-on** — confirms the 13" Dev Kit is the Caster/Spartan-6
  board with a 41 Hz panel in that sample, and that the 6" and 13" versions
  share the same controller hardware.
  https://electrotrio.com/modos-paper-monitor-an-open-hardware-dev-kit-brings-75-hz-refresh-to-e-paper-displays/
- **The physics, stated plainly** — Hacker News discussion of high-refresh
  e-paper: ink costs a lot of power to move and ~none to hold, so the win is
  duty-cycle dependent; a driver board in continuous use "draws about 1 to
  1.5 W". https://news.ycombinator.com/item?id=45185756

## The host machine

- **Framework Laptop 13 mainboard** — `FRANMDCP05`, Ryzen 7040 series. The
  display expansion connector is eDP; this Modos kit arrives over USB-C
  DP-Alt, which is why it enumerates as `DP-3` and why the internal panel
  (`card1-eDP-1`) is absent.
  https://github.com/FrameworkComputer/Framework-Laptop-13
- **Framework community: "Exploring idle power consumption"** — LCD baseline
  for calibration: 1.84 W total idle at 0% backlight, 4.30 W at 100%; Wi-Fi
  ~0.1 W; a BT headset connected but idle costs ~2 W. Different OS and tuning,
  so order-of-magnitude only.
  https://community.frame.work/t/exploring-idle-power-consumption/74690
- **Framework community: display power complaints** — useful for the
  "powertop overestimates" lesson: powertop claimed 9 W of backlight on a
  panel whose datasheet maximum is 3.4 W.
  https://community.frame.work/t/amd-framework-13-7640u-display-is-using-9w-of-battery-at-30/46219
- **Framework community: 2.8K panel power** — real-world example that a
  bigger, faster LCD costs several watts more, i.e. panel refresh rate and
  resolution are the levers, not the backlight alone.
  https://community.frame.work/t/responded-framework-13-2-8k-display-high-power-use/58265

## Tooling, checked on this machine rather than assumed

- `extra/powertop 2.16-1` exists in the repo but is not installed. Not
  installed deliberately: its display model is a backlight model and
  `/sys/class/backlight` is empty here, so its "Display backlight" line would
  be an extrapolation. Its R half needs root.
- `upower` is installed; its `energy-rate` proved to be a stale smoothed
  average (see README traps).
- `/sys/class/powercap/intel-rapl` exists (domains `package-0`, `core`) but
  `energy_uj` is mode 0400 root-only, and the RAPL MSRs on AMD are indicative
  rather than calibrated.
- `btop` is installed; without root it cannot read RAPL, so it adds nothing
  here.
