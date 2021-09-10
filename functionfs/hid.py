# This file is part of python-functionfs
# Copyright (C) 2018-2021  Vincent Pelletier <plr.vincent@gmail.com>
#
# python-functionfs is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# python-functionfs is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with python-functionfs.  If not, see <http://www.gnu.org/licenses/>.
"""
HID-specific definitions.
Built partly from linux/hid.h and from spec.
"""
import ctypes
from .common import USBDescriptorHeader, u8, le16
from . import ch9

# USB HID (Human Interface Device) interface class code

USB_INTERFACE_CLASS_HID = ch9.USB_CLASS_HID

# USB HID interface subclass and protofol codes

USB_INTERFACE_SUBCLASS_NONE = 0
USB_INTERFACE_SUBCLASS_BOOT = 1
USB_INTERFACE_PROTOCOL_NONE = 0
USB_INTERFACE_PROTOCOL_KEYBOARD = 1
USB_INTERFACE_PROTOCOL_MOUSE = 2

# HID class requests

HID_REQ_GET_REPORT = 0x01
HID_REQ_GET_IDLE = 0x02
HID_REQ_GET_PROTOCOL = 0x03
HID_REQ_SET_REPORT = 0x09
HID_REQ_SET_IDLE = 0x0a
HID_REQ_SET_PROTOCOL = 0x0b

# HID class descriptor types

HID_DT_HID = ch9.USB_TYPE_CLASS | 1
HID_DT_REPORT = ch9.USB_TYPE_CLASS | 2
HID_DT_PHYSICAL = ch9.USB_TYPE_CLASS | 3

HID_MAX_DESCRIPTOR_SIZE = 4096

class _USBHIDDescriptor(USBDescriptorHeader):
    """
    HID_DT_HID: HID descriptor
    """
    _bDescriptorType = HID_DT_HID
    _fields_ = [
        ('bcdHID', le16),
        ('bCountryCode', u8),
        ('bNumDescriptors', u8),
    ]

class USBHIDDescriptorTail(ctypes.LittleEndianStructure):
    """
    HID_DT_HID descriptor ends with a variable number of these 2 fields.
    """
    _pack_ = 1
    _fields_ = [
        ('bDescriptorType', u8),
        ('wDescriptorLength', le16),
    ]

def getUSBHIDDescriptorClass(hid_descriptor_count=1):
    """
    Concatenate as many USBHIDDescriptorTail as requested to USBHIDDescriptor,
    and return resulting class.

    hid_descriptor_count (int)
        Number of HID descrtiptor entries.
        Should be at least 1, as there must be one USB_DT_REPORT descriptor.
        Note: as of this writing (circa 4.18), f_fs only supports exactly
        1 HID descriptor entry.
    """
    return type(
        'USBHIDDescriptorWithTail',
        (_USBHIDDescriptor, ),
        {
            '_fields_': [
                ('tail', USBHIDDescriptorTail * hid_descriptor_count),
            ]
        }
    )
