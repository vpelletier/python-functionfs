Pythonic API for linux's functionfs.

functionfs is part of the usb gadget subsystem. Together with usb_gadget's
configfs integration, allows userland to declare and implement an USB device.

Requirements
============

- a linux-capable computer with a USB controller able to act as a device
  Ex: Raspberry Pi zero, Intel Edison

- the linux kernel built with the following enabled:
  CONFIG_USB_CONFIGFS
  CONFIG_USB_FUNCTIONFS
  (plus all needed peripheral drivers, including the usb device controller)
