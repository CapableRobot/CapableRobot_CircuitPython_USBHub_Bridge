# Capable Robot CircuitPython USBHub Bridge

This library has two functions:

- It provides access to internal state of the Capable Robot USB Hub, allowing you to monitor and control the Hub from an upstream computer.
- It creates a transparent CircuitPython Bridge, allowing unmodified CircuitPython code to run on the host computer and interact with I2C devices attached to the USB Hub.

## Installing Dependencies

	pip3 install pyusb construct pyyaml click

On Linux, the the udev permission system will likely prevent normal users from accessing the USB Hub's endpoint which allows for Hub Monitoring, Control, and I2C Briding.  To resolve this, install the provided udev rule:

```
sudo cp 50-capablerobot-usbhub.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger
```

Then unplug and replug your USB Hub.  Note, the provided udev rule allows all system users to access the Hub, but can be changed to a specific user or user group.

## Working Functionality

- Reading USB Hub registers over USB and decoding of register data.
- Writing USB Hub registers over USB.
- Reading & writing I2C data thru the Hub.
- Python API to control and read the two GPIO pins.
- CircuitPython I2C Bridge.  
- CircuitPython SPI Bridge.

## Not Working / Not Implemented Yet

_No known errata at this time_

## Contributing 

Contributions are welcome! Please read our 
[Code of Conduct](https://github.com/capablerobot/CapableRobot_CircuitPython_USBHub_Bridge/blob/master/CODE_OF_CONDUCT.md)
before contributing to help this project stay welcoming.