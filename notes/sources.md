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

## Published Modos power figures, and how they compare

Three independent sets of numbers exist. All of them were gathered by people
other than us, and all three land in the same place.

### 1. Modos' own measurement, per rail (the good one)

`Modos-Labs/Glider` PR #15 ("Implement Native Standby Mode and DisplayPort
Wakeup Handshake"), in a review comment. Measured with the board's own INA3221
current/voltage sensors, before and after entering standby:

| Rail / Component | Active (mW) | Standby (mW) | Delta (mW) |
|---|---|---|---|
| EPD high-voltage rails | 92.7 | 0.0 | -92.7 |
| Video receiver (VIDEO IN) | 378.8 | 21.2 | -357.6 |
| FPGA core | 159.7 | 88.2 | -71.5 |
| DDR3 memory | 62.6 | 24.5 | -38.1 |
| MCU + IO | 574.9 | 502.2 | -72.7 |
| **Total board** | **1268.7** | **636.1** | **-632.6 (-50%)** |

The author notes this PR was superseded by upstream's native power manager.
Note the MCU is the single biggest consumer even in standby (502 mW) — it
stays awake to keep the USB TTY responsive.
  https://github.com/Modos-Labs/Glider/pull/15

### 2. An independent USB-C power meter

A comment on lobste.rs ("How I made a 60fps Eink monitor, the Modos Flow"),
using an ATORCH AT085 USB-C interposer, explicitly labelled approximate:
**~1.5 W** with a near-static screen, **~1.9 W** scrolling web content, and
**~2 W** with video playing and full-screen wipes. No difference noticed
between reading/typing/watching/browsing modes.
  https://lobste.rs/s/wnn1ul/how_i_made_60fps_eink_monitor_modos_flow

### 3. Modos' 2022 product blog (first generation, Micro-USB)

"MicroUSB power input (consumes 1.5W~2W continuously)".
  https://www.modos.tech/blog/modos-paper-monitor

### Refresh, the part that is expensive but brief

From the Glider technical deep dive: the EPD supply is rated for **1 A peak on
the ±15 V rails**, and "modern high-resolution panels can consume **>20 W
peak**". Those are peaks during a repaint, not averages — which is exactly why
holding a static image is the cheap case and refreshing is not. The same
document gives power as one of the three explicit reasons for choosing a
Spartan-6 over a 7-series: super-low leakage at small design sizes, and an
FPGA suspend feature.
  https://www.crowdsupply.com/modos-tech/modos-paper-monitor/updates/a-technical-deep-dive-into-glider

### Relative claims

CrowdSupply's own comparison table rates the Modos Paper Dev Kit's power
consumption as **"High"** against Waveshare e-Paper HAT, Inkplate 6 MOTION and
EPDiy, all rated "Low" — the price paid for an FPGA and 75 Hz. Separately, the
Flow comparison post states the Dev Kit "uses only half as much power as Flow"
in the default configuration.
  https://www.crowdsupply.com/modos-tech/modos-paper-monitor/
  https://www.crowdsupply.com/modos-tech/modos-flow/updates/comparing-the-paper-dev-kit-with-flow

## The board's own telemetry: present in hardware, hidden in firmware

The claim that this display offers no telemetry is only true of the *Linux*
side. The board carries **three INA3221** rail monitors, polled every 100 ms by
the STM32H750 (`fw/User/power.c`), and the firmware has a shell command that
prints per-rail power as current/average/**max** in mW, grouped as `MCU + IO`,
`FPGA DDR`, `FPGA CORE`, `VIDEO IN`, `EPD HV` — the same five groups as
Modos' published table.

**But that command is compiled out of the release build.** The function
`shell_sensor()` sits behind `#ifdef GLIDER_DIAGNOSTIC_SHELL`
(`fw/User/shell/shell_cmds.c`), and our board rejects it:

    # sensor
    Invalid command, type 'help' for help

The complete command set in build 0.1 (Sep 23 2026) is `help`, `power`,
`setcfg`, `setres`, `syslog`, `ver`. `power` is `[status|off]` and reports the
suspend state machine, not numbers.

So per-rail watts would require building and flashing the firmware from the
public Glider repo with `-DGLIDER_DIAGNOSTIC_SHELL`. That is a real project
with real risk, and it is the only route to the per-rail breakdown.

### What the board will tell us for free

`syslog` is readable and confirms the hardware, the power contract, and the
idle rail voltages. From our board, at startup:

    [ 0.414]  INA 40: Mfg ID = 5449, Die ID = 3220
    [ 0.415]  INA 41: Mfg ID = 5449, Die ID = 3220
    [ 0.416]  INA 42: Mfg ID = 5449, Die ID = 3220
    [ 0.676]  C0 entering state PD_STATE_SNK_READY
    [ 0.676]  Setting input current limit to 5000 V 1500 mA
    [ 1.289]  Bitstream loading took 593 ms
    [ 1.597]  VN: 0.06 V
    [ 1.797]  VN: -15.02 V     VGL: -20.34 V
    [ 1.997]  VP: 14.91 V      VGH: 25.06 V
    [ 2.197]  VCOM: -2.35 V
    [ 2.219]  Input status 19 debug 8e, measured 1600 x 1200, total 1680 x 1242

Three INA3221s present (TI, die 3220), panel timing 1600x1200 of 1680x1242,
and the EPD high-voltage rails sitting energised at +/-15 V and +25 V — the
92.7 mW of "EPD HV" in Modos' active-mode table, still being drawn to hold a
still image.

**The `5.0 V / 1.5 A` reading is the PD contract, now confirmed from the board
itself**: the log shows the board negotiating `Req C0 [1] 5000mV 1500mA` and
then setting its input current limit to exactly that. It is the ceiling the
board asks for (7.5 W), never the live draw — which is why the UCSI
`power_now` values on this laptop are useless for this measurement and why the
differential method was necessary.

### The board does suspend, and video loss is the trigger

    # power status
    state: active
    last reason: video-loss
    suspend count: 1
    resume count: 1

The Glider suspends itself when the video signal goes away. This is consistent
with the status LED going dark when the laptop sleeps, and it means the
firmware's standby path is reachable in normal use — the open question is only
whether *this* build takes the full 636 mW figure or stops partway.
