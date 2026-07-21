from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from gpiozero import PWMOutputDevice


DEFAULT_GPIO = 14
DEFAULT_FREQUENCY = 100
DEFAULT_INTERVAL = 2.0


def cpu_temperature_c() -> float:
    for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
        try:
            if zone.joinpath("type").read_text().strip() == "cpu-thermal":
                return int(zone.joinpath("temp").read_text().strip()) / 1000
        except OSError:
            continue
    raise RuntimeError("cpu_temperature_unavailable")


def fan_speed(temp_c: float) -> float:
    """Return PWM duty cycle from 0.0 to 1.0.

    A small hysteresis-free curve is intentional here: it is simple, predictable,
    and updates slowly enough to avoid distracting speed changes.
    """
    if temp_c < 40:
        return 0.0
    if temp_c < 50:
        return 0.45
    if temp_c < 60:
        return 0.70
    if temp_c < 65:
        return 0.90
    return 1.0


def run(gpio: int, frequency: int, interval: float) -> None:
    fan = PWMOutputDevice(gpio, frequency=frequency, initial_value=0.0)
    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            fan.value = fan_speed(cpu_temperature_c())
            time.sleep(interval)
    finally:
        fan.value = 0.0
        fan.close()


def run_sweep(gpio: int, frequency: int, step_seconds: float) -> None:
    fan = PWMOutputDevice(gpio, frequency=frequency, initial_value=0.0)
    running = True
    speeds = [0.2, 0.5, 1.0, 0.5, 0.2]

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            for speed in speeds:
                if not running:
                    break
                fan.value = speed
                time.sleep(step_seconds)
    finally:
        fan.value = 0.0
        fan.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="PiWatch PWM fan controller")
    parser.add_argument("--mode", choices=["thermal", "sweep"], default="thermal", help="Fan control mode")
    parser.add_argument("--gpio", type=int, default=DEFAULT_GPIO, help="BCM GPIO pin for PWM control")
    parser.add_argument("--frequency", type=int, default=DEFAULT_FREQUENCY, help="PWM frequency in Hz")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="Temperature polling interval")
    parser.add_argument("--step-seconds", type=float, default=5.0, help="Sweep mode dwell time per speed")
    args = parser.parse_args()
    if args.mode == "sweep":
        run_sweep(args.gpio, args.frequency, args.step_seconds)
    else:
        run(args.gpio, args.frequency, args.interval)


if __name__ == "__main__":
    main()
