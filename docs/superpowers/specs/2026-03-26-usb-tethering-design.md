# USB-C Tethering Transport

## Summary

Add USB-C tethering as a fourth transport option. Since USB tethering creates a standard network interface (`usb0`) on the Pi, the existing HTTP endpoint already works for GPS data. The new transport monitors the `usb0` interface health and reports it to the dashboard.

## 1. USBTransport

New file: `src/slower/transport/usb.py`

A `USBTransport` class that:
- Runs a background thread checking if a configurable network interface (default `usb0`) exists and is up
- Check interval: every 3 seconds
- Uses `TransportHealth` for state tracking (healthy/lost)
- Interface detection: reads `/sys/class/net/{interface}/operstate` on Linux. If the file exists and contains `up`, the interface is active.
- On non-Linux (macOS dev), gracefully reports "unknown" state
- Implements the `GPSTransport` protocol (start/stop), though it does not call `GPSProvider.update()` directly since data flows through the HTTP endpoint

## 2. Config

Add to `TransportConfig` in `config.py`:
```python
usb: bool = True
usb_interface: str = "usb0"
```

## 3. Dashboard

Add a `USB` status chip to `index.html`, same pattern as the existing WiFi/BLE/SPP chips. Update `updateUI()` to call `updateTransportChip('chipUsb', 'USB', state.transport_states.usb)`.

## 4. Main Entry Point

In `main.py`, after the existing transport wiring, add USB transport startup:
```python
if config.transports.usb:
    from slower.transport.usb import USBTransport
    usb_transport = USBTransport(interface=config.transports.usb_interface)
    usb_transport.start(gps)
    connection_monitor.add_gps_transport("usb")
```

## 5. README

Update Hardware Required section to list USB-C as a connectivity option. Update the architecture diagram to include USB-C.

## 6. Non-Goals

- No new GPS data path (HTTP handles it)
- No companion app
- No USB serial/CDC ACM protocol
- No ADB bridging
