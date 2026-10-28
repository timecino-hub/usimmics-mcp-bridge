"""Voltage-spectrum power conversion shared by reports and previews."""
import math
from .core import BridgeError


def voltage_power_dbm(magnitude, frequency_hz, impedance=50):
    """DC samples are voltages; nonzero-frequency HB samples are peak amplitudes.

    Return None for zero power (negative infinity in dBm). Reject nonfinite or
    negative inputs. Log-domain calculation avoids squaring overflow/underflow.
    """
    for name, value in [('magnitude', magnitude), ('frequency_hz', frequency_hz), ('impedance', impedance)]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise BridgeError(name+' must be a finite number')
    if magnitude<0 or frequency_hz<0 or impedance<=0:
        raise BridgeError('Magnitude/frequency must be nonnegative and impedance positive')
    if magnitude==0: return None
    return 20*math.log10(magnitude)-10*math.log10(impedance)+30-(0 if frequency_hz==0 else 10*math.log10(2))
