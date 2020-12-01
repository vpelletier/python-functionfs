Pythonic API for linux's functionfs.

functionfs is part of the usb gadget subsystem. Together with usb_gadget's
configfs integration, allows userland to declare and implement an USB device.

Requirements
============

- A linux-capable computer with a USB controller able to act as a device.
  Ex: Raspberry Pi zero, Intel Edison

- The linux kernel built with CONFIG_USB_CONFIGFS_F_FS enabled,
  plus all needed peripheral drivers, including the usb device controller.

- python-libaio, which itself depends on libaio.
