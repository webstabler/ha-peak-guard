"""Operating-mode selection for bidirectional battery peak control."""

from __future__ import annotations

from typing import Any

from .const import (
    DEFAULT_INTERVAL_BOUNDARY_MODE_GRACE_SECONDS,
    DEFAULT_SOLAR_SURPLUS_FILTER_TIME_S,
    DEFAULT_SOLAR_SURPLUS_HYSTERESIS_W,
)


class BatteryModeManager:
    """Select discharge, solar charging, grid charging, idle, or self-consumption.

    Grid charging is inhibited for the remainder of a quarter-hour after a
    discharge request has occurred. Peak protection takes priority over
    self-consumption operation. Positive power means discharge; negative
    means charge.
    """

    def __init__(self) -> None:
        self.interval_id: int | None = None
        self.discharge_started_this_block = False
        self.mode = "idle"
        self.reason = "waiting_for_control_data"
        self.last_charge_setpoint_w = 0.0
        self.grid_charge_latched = False
        self.solar_charge_latched = False
        self.filtered_solar_surplus_w: float | None = None
        self.solar_surplus_filter_ts: float | None = None

    def filter_solar_surplus(
        self,
        solar_surplus_w: float,
        *,
        now_ts: float,
        filter_time_s: float = DEFAULT_SOLAR_SURPLUS_FILTER_TIME_S,
    ) -> float:
        """Return an exponentially smoothed solar-surplus measurement."""
        solar_surplus_w = max(0.0, float(solar_surplus_w))
        if (
            self.filtered_solar_surplus_w is None
            or self.solar_surplus_filter_ts is None
            or filter_time_s <= 0.0
        ):
            filtered = solar_surplus_w
        else:
            dt = max(0.0, now_ts - self.solar_surplus_filter_ts)
            alpha = dt / (filter_time_s + dt) if dt > 0.0 else 0.0
            filtered = self.filtered_solar_surplus_w + alpha * (
                solar_surplus_w - self.filtered_solar_surplus_w
            )

        self.filtered_solar_surplus_w = filtered
        self.solar_surplus_filter_ts = now_ts
        return filtered

    def should_retain_native_mode(
        self,
        *,
        sample_timestamp: float,
        now_timestamp: float,
        grace_seconds: float = DEFAULT_INTERVAL_BOUNDARY_MODE_GRACE_SECONDS,
    ) -> bool:
        """Keep native control only across a brief quarter-hour sample lag."""
        if self.mode not in ("solar_charge", "self_consumption"):
            return False

        sample_interval_id = int(sample_timestamp // 900)
        now_interval_id = int(now_timestamp // 900)
        sample_age_seconds = now_timestamp - sample_timestamp
        return (
            self.interval_id == sample_interval_id
            and now_interval_id == sample_interval_id + 1
            and 0.0 <= sample_age_seconds <= grace_seconds
        )

    def _update_interval(self, interval_id: int) -> None:
        if self.interval_id != interval_id:
            self.interval_id = interval_id
            self.discharge_started_this_block = False

    def select_mode(
        self,
        *,
        interval_id: int,
        discharge_required: bool,
        discharge_active: bool,
        battery_soc: float,
        grid_charge_soc_limit: float,
        max_charge_soc: float,
        solar_surplus_w: float,
        available_peak_headroom_w: float,
        grid_charge_soc_hysteresis: float = 0.0,
        minimum_solar_surplus_w: float = 200.0,
        solar_surplus_hysteresis_w: float = DEFAULT_SOLAR_SURPLUS_HYSTERESIS_W,
        minimum_grid_charge_headroom_w: float = 500.0,
    ) -> tuple[str, str]:
        """Select a mode and return (mode, reason)."""
        self._update_interval(interval_id)

        if discharge_required:
            self.grid_charge_latched = False
            if discharge_active:
                self.discharge_started_this_block = True
                self.mode = "discharge"
                self.reason = "peak_protection"
            else:
                self.mode = "idle"
                self.reason = "peak_protection_waiting_or_unavailable"
            return self.mode, self.reason

        if battery_soc > max_charge_soc:
            self.grid_charge_latched = False
            self.solar_charge_latched = False
            self.mode = "self_consumption"
            self.reason = "above_max_charge_soc"
            return self.mode, self.reason

        grid_charge_was_active = self.mode == "grid_charge"
        headroom_sufficient = (
            available_peak_headroom_w >= minimum_grid_charge_headroom_w
            if not grid_charge_was_active
            else available_peak_headroom_w > 0
        )

        grid_charge_soc_stop = min(
            grid_charge_soc_limit + grid_charge_soc_hysteresis,
            max_charge_soc,
        )
        if self.grid_charge_latched and battery_soc >= grid_charge_soc_stop:
            self.grid_charge_latched = False

        solar_charge_start_w = max(0.0, minimum_solar_surplus_w)
        solar_charge_stop_w = max(
            0.0, solar_charge_start_w - solar_surplus_hysteresis_w
        )

        if self.solar_charge_latched and (
            solar_surplus_w <= solar_charge_stop_w
            or battery_soc >= max_charge_soc
        ):
            self.solar_charge_latched = False

        if (
            not self.solar_charge_latched
            and battery_soc < max_charge_soc
            and solar_surplus_w > 0.0
            and solar_surplus_w >= solar_charge_start_w
        ):
            self.solar_charge_latched = True

        if self.solar_charge_latched:
            self.mode = "solar_charge"
            self.reason = (
                "solar_surplus"
                if (
                    battery_soc < max_charge_soc
                    and solar_surplus_w >= solar_charge_start_w
                )
                else "solar_charge_hysteresis"
            )
            return self.mode, self.reason

        # Grid charging is the only mode blocked after a discharge request.
        if self.discharge_started_this_block:
            self.grid_charge_latched = False
            self.mode = "idle"
            self.reason = "grid_charging_blocked_after_discharge"
        elif not self.grid_charge_latched and (
            battery_soc >= grid_charge_soc_limit
            or battery_soc >= max_charge_soc
        ):
            self.mode = "idle"
            self.reason = "grid_charge_soc_limit"
        elif not headroom_sufficient:
            self.mode = "idle"
            self.reason = "insufficient_peak_headroom"
        else:
            self.grid_charge_latched = True
            self.mode = "grid_charge"
            self.reason = "available_peak_headroom"

        return self.mode, self.reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval_id": self.interval_id,
            "discharge_started_this_block": self.discharge_started_this_block,
            "mode": self.mode,
            "reason": self.reason,
            "last_charge_setpoint_w": self.last_charge_setpoint_w,
            "grid_charge_latched": self.grid_charge_latched,
            "solar_charge_latched": self.solar_charge_latched,
            "filtered_solar_surplus_w": self.filtered_solar_surplus_w,
            "solar_surplus_filter_ts": self.solar_surplus_filter_ts,
        }

    def restore_state(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        self.interval_id = data.get("interval_id")
        self.discharge_started_this_block = bool(
            data.get("discharge_started_this_block", False)
        )
        restored_mode = str(data.get("mode", "idle"))
        self.mode = (
            "self_consumption" if restored_mode == "default" else restored_mode
        )
        self.reason = str(data.get("reason", "restored"))
        self.last_charge_setpoint_w = float(
            data.get("last_charge_setpoint_w", 0.0)
        )
        self.grid_charge_latched = bool(data.get("grid_charge_latched", False))
        self.solar_charge_latched = bool(
            data.get(
                "solar_charge_latched",
                data.get("mode") == "solar_charge",
            )
        )
        filtered_surplus = data.get("filtered_solar_surplus_w")
        self.filtered_solar_surplus_w = (
            None if filtered_surplus is None else float(filtered_surplus)
        )
        filter_ts = data.get("solar_surplus_filter_ts")
        self.solar_surplus_filter_ts = (
            None if filter_ts is None else float(filter_ts)
        )
