"""Single-node battery thermal model.

    C * dT/dt = Qgen - Qcool
    Qgen  = I^2 * Rint
    Qcool = Kcool * u * (T - Ta)

Discrete update (forward Euler):
    T_next = T + (dt / C) * (Qgen - Qcool)

Units: T in degC, C in J/K, Q in W, dt in s, I in A, Rint in ohm, Kcool in W/K.
Default values are illustrative assumptions, not data for a real battery pack.
"""

from dataclasses import dataclass, asdict
import math


@dataclass
class BatteryParams:
    capacitance: float = 1.0e5     # C, J/K   (about 100 kg of cells at ~1000 J/kg/K)
    resistance: float = 0.1        # Rint, ohm (effective pack resistance)
    k_cool: float = 1000.0         # Kcool, W/K at full cooling (u = 1)
    ambient: float = 30.0          # Ta, degC
    p_cool_max: float = 2000.0     # Pmax, W  electrical power of cooler at u = 1

    def validate(self, dt: float) -> None:
        """Raise ValueError for parameters that would break the simulation."""
        for name, value in asdict(self).items():
            if not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if self.capacitance <= 0:
            raise ValueError("capacitance must be > 0")
        if self.resistance < 0:
            raise ValueError("resistance must be >= 0")
        if self.k_cool < 0:
            raise ValueError("k_cool must be >= 0")
        if self.p_cool_max < 0:
            raise ValueError("p_cool_max must be >= 0")
        if not (math.isfinite(dt) and dt > 0):
            raise ValueError("time step dt must be > 0")
        # Forward Euler is stable only if dt * Kcool / C < 1.
        if dt * self.k_cool / self.capacitance >= 1.0:
            raise ValueError(
                f"time step too large: dt * Kcool / C = {dt * self.k_cool / self.capacitance:.2f} "
                "must be < 1 (reduce dt or Kcool, or increase C)"
            )

    def as_dict(self) -> dict:
        return asdict(self)


def heat_generation(current: float, p: BatteryParams) -> float:
    """Joule heating Qgen = I^2 * Rint, in W."""
    return current ** 2 * p.resistance


def heat_removed(temp: float, u: float, p: BatteryParams) -> float:
    """Cooling heat flow Qcool = Kcool * u * (T - Ta), in W.

    If the battery is colder than ambient this is negative (ambient warms it).
    """
    return p.k_cool * u * (temp - p.ambient)


def step(temp: float, current: float, u: float, dt: float, p: BatteryParams) -> float:
    """Advance battery temperature by one time step."""
    if not 0.0 <= u <= 1.0:
        raise ValueError(f"cooling command u must be in [0, 1], got {u}")
    q_gen = heat_generation(current, p)
    q_cool = heat_removed(temp, u, p)
    temp_next = temp + (dt / p.capacitance) * (q_gen - q_cool)
    if not math.isfinite(temp_next):
        raise ValueError("temperature became non-finite; check parameters")
    return temp_next


def cooling_power(u: float, p: BatteryParams) -> float:
    """Electrical power drawn by the cooler, Pcool = Pmax * u^3 (assumed fan/pump law), in W."""
    return p.p_cool_max * u ** 3
