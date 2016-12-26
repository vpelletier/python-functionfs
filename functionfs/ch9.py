# This file is part of python-functionfs
# Copyright (C) 2016  Vincent Pelletier <plr.vincent@gmail.com>
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
import ctypes
from .common import USBDescriptorHeader, u8, le16

# Translated from linux/usb/ch9.h
# CONTROL REQUEST SUPPORT

# USB directions
#
# This bit flag is used in endpoint descriptors' bEndpointAddress field.
# It's also one of three fields in control requests bRequestType.
USB_DIR_OUT = 0 # to device
USB_DIR_IN = 0x80 # to host

# USB types, the second of three bRequestType fields
USB_TYPE_MASK = (0x03 << 5)
USB_TYPE_STANDARD = (0x00 << 5)
USB_TYPE_CLASS = (0x01 << 5)
USB_TYPE_VENDOR = (0x02 << 5)
USB_TYPE_RESERVED = (0x03 << 5)

# USB recipients, the third of three bRequestType fields
USB_RECIP_MASK = 0x1f
USB_RECIP_DEVICE = 0x00
USB_RECIP_INTERFACE = 0x01
USB_RECIP_ENDPOINT = 0x02
USB_RECIP_OTHER = 0x03
# From Wireless USB 1.0
USB_RECIP_PORT = 0x04
USB_RECIP_RPIPE = 0x05

# Standard requests, for the bRequest field of a SETUP packet.
#
# These are qualified by the bRequestType field, so that for example
# TYPE_CLASS or TYPE_VENDOR specific feature flags could be retrieved
# by a GET_STATUS request.
USB_REQ_GET_STATUS = 0x00
USB_REQ_CLEAR_FEATURE = 0x01
USB_REQ_SET_FEATURE = 0x03
USB_REQ_SET_ADDRESS = 0x05
USB_REQ_GET_DESCRIPTOR = 0x06
USB_REQ_SET_DESCRIPTOR = 0x07
USB_REQ_GET_CONFIGURATION = 0x08
USB_REQ_SET_CONFIGURATION = 0x09
USB_REQ_GET_INTERFACE = 0x0A
USB_REQ_SET_INTERFACE = 0x0B
USB_REQ_SYNCH_FRAME = 0x0C
USB_REQ_SET_SEL = 0x30
USB_REQ_SET_ISOCH_DELAY = 0x31

USB_REQ_SET_ENCRYPTION = 0x0D # Wireless USB
USB_REQ_GET_ENCRYPTION = 0x0E
USB_REQ_RPIPE_ABORT = 0x0E
USB_REQ_SET_HANDSHAKE = 0x0F
USB_REQ_RPIPE_RESET = 0x0F
USB_REQ_GET_HANDSHAKE = 0x10
USB_REQ_SET_CONNECTION = 0x11
USB_REQ_SET_SECURITY_DATA = 0x12
USB_REQ_GET_SECURITY_DATA = 0x13
USB_REQ_SET_WUSB_DATA = 0x14
USB_REQ_LOOPBACK_DATA_WRITE = 0x15
USB_REQ_LOOPBACK_DATA_READ = 0x16
USB_REQ_SET_INTERFACE_DS = 0x17

# specific requests for USB Power Delivery
USB_REQ_GET_PARTNER_PDO = 20
USB_REQ_GET_BATTERY_STATUS = 21
USB_REQ_SET_PDO = 22
USB_REQ_GET_VDM = 23
USB_REQ_SEND_VDM = 24

# The Link Power Management (LPM) ECN defines USB_REQ_TEST_AND_SET command,
# used by hubs to put ports into a new L1 suspend state, except that it
# forgot to define its number ...

# USB feature flags are written using USB_REQ_{CLEAR,SET}_FEATURE, and
# are read as a bit array returned by USB_REQ_GET_STATUS.  (So there
# are at most sixteen features of each type.)  Hubs may also support a
# new USB_REQ_TEST_AND_SET_FEATURE to put ports into L1 suspend.
USB_DEVICE_SELF_POWERED = 0 # (read only)
USB_DEVICE_REMOTE_WAKEUP = 1 # dev may initiate wakeup
USB_DEVICE_TEST_MODE = 2 # (wired high speed only)
USB_DEVICE_BATTERY = 2 # (wireless)
USB_DEVICE_B_HNP_ENABLE = 3 # (otg) dev may initiate HNP
USB_DEVICE_WUSB_DEVICE = 3 # (wireless)
USB_DEVICE_A_HNP_SUPPORT = 4 # (otg) RH port supports HNP
USB_DEVICE_A_ALT_HNP_SUPPORT = 5 # (otg) other RH port does
USB_DEVICE_DEBUG_MODE = 6 # (special devices only)

# Test Mode Selectors
# See USB 2.0 spec Table 9-7
TEST_J = 1
TEST_K = 2
TEST_SE0_NAK = 3
TEST_PACKET = 4
TEST_FORCE_EN = 5

# New Feature Selectors as added by USB 3.0
# See USB 3.0 spec Table 9-7
USB_DEVICE_U1_ENABLE = 48 # dev may initiate U1 transition
USB_DEVICE_U2_ENABLE = 49 # dev may initiate U2 transition
USB_DEVICE_LTM_ENABLE = 50 # dev may send LTM
USB_INTRF_FUNC_SUSPEND = 0 # function suspend

USB_INTR_FUNC_SUSPEND_OPT_MASK = 0xFF00
# Suspend Options, Table 9-8 USB 3.0 spec
USB_INTRF_FUNC_SUSPEND_LP = (1 << (8 + 0))
USB_INTRF_FUNC_SUSPEND_RW = (1 << (8 + 1))

# Interface status, Figure 9-5 USB 3.0 spec
USB_INTRF_STAT_FUNC_RW_CAP = 1
USB_INTRF_STAT_FUNC_RW = 2

USB_ENDPOINT_HALT = 0 # IN/OUT will STALL

# Bit array elements as returned by the USB_REQ_GET_STATUS request.
USB_DEV_STAT_U1_ENABLED = 2 # transition into U1 state
USB_DEV_STAT_U2_ENABLED = 3 # transition into U2 state
USB_DEV_STAT_LTM_ENABLED = 4 # Latency tolerance messages

class USBCtrlRequest(ctypes.LittleEndianStructure):
    """
    struct usb_ctrlrequest - SETUP data for a USB device control request
    @bRequestType: matches the USB bmRequestType field
    @bRequest: matches the USB bRequest field
    @wValue: matches the USB wValue field (le16 byte order)
    @wIndex: matches the USB wIndex field (le16 byte order)
    @wLength: matches the USB wLength field (le16 byte order)

    This structure is used to send control requests to a USB device.  It matches
    the different fields of the USB 2.0 Spec section 9.3, table 9-2.  See the
    USB spec for a fuller description of the different fields, and what they are
    used for.

    Note that the driver for any interface can issue control requests.
    For most devices, interfaces don't coordinate with each other, so
    such requests may be made at any time.
    """
    _pack_ = 1
    _fields_ = [
        ('bRequestType', u8),
        ('bRequest', u8),
        ('wValue', le16),
        ('wIndex', le16),
        ('wLength', le16),
    ]

# STANDARD DESCRIPTORS ... as returned by GET_DESCRIPTOR, or
# (rarely) accepted by SET_DESCRIPTOR.
#
# Note that all multi-byte values here are encoded in little endian
# byte order "on the wire".  Within the kernel and when exposed
# through the Linux-USB APIs, they are not converted to cpu byte
# order; it is the responsibility of the client code to do this.
# The single exception is when device and configuration descriptors (but
# not other descriptors) are read from usbfs (i.e. /proc/bus/usb/BBB/DDD);
# in this case the fields are converted to host endianness by the kernel.

# Descriptor types ... USB 2.0 spec table 9.5
USB_DT_DEVICE = 0x01
USB_DT_CONFIG = 0x02
USB_DT_STRING = 0x03
USB_DT_INTERFACE = 0x04
USB_DT_ENDPOINT = 0x05
USB_DT_DEVICE_QUALIFIER = 0x06
USB_DT_OTHER_SPEED_CONFIG = 0x07
USB_DT_INTERFACE_POWER = 0x08
# these are from a minor usb 2.0 revision (ECN)
USB_DT_OTG = 0x09
USB_DT_DEBUG = 0x0a
USB_DT_INTERFACE_ASSOCIATION = 0x0b
# these are from the Wireless USB spec
USB_DT_SECURITY = 0x0c
USB_DT_KEY = 0x0d
USB_DT_ENCRYPTION_TYPE = 0x0e
USB_DT_BOS = 0x0f
USB_DT_DEVICE_CAPABILITY = 0x10
USB_DT_WIRELESS_ENDPOINT_COMP = 0x11
USB_DT_WIRE_ADAPTER = 0x21
USB_DT_RPIPE = 0x22
USB_DT_CS_RADIO_CONTROL = 0x23
# From the T10 UAS specification
USB_DT_PIPE_USAGE = 0x24
# From the USB 3.0 spec
USB_DT_SS_ENDPOINT_COMP = 0x30
# From the USB 3.1 spec
USB_DT_SSP_ISOC_ENDPOINT_COMP = 0x31

# Conventional codes for class-specific descriptors.  The convention is
# defined in the USB "Common Class" Spec (3.11).  Individual class specs
# are authoritative for their usage, not the "common class" writeup.
USB_DT_CS_DEVICE = (USB_TYPE_CLASS | USB_DT_DEVICE)
USB_DT_CS_CONFIG = (USB_TYPE_CLASS | USB_DT_CONFIG)
USB_DT_CS_STRING = (USB_TYPE_CLASS | USB_DT_STRING)
USB_DT_CS_INTERFACE = (USB_TYPE_CLASS | USB_DT_INTERFACE)
USB_DT_CS_ENDPOINT = (USB_TYPE_CLASS | USB_DT_ENDPOINT)

# USBDescriptorHeader: from common.py

class USBDeviceDescriptor(USBDescriptorHeader):
    """
    USB_DT_DEVICE: Device descriptor
    """
    _bDescriptorType = USB_DT_DEVICE
    _fields_ = [
        ('bcdUSB', le16),
        ('bDeviceClass', u8),
        ('bDeviceSubClass', u8),
        ('bDeviceProtocol', u8),
        ('bMaxPacketSize0', u8),
        ('idVendor', le16),
        ('idProduct', le16),
        ('bcdDevice', le16),
        ('iManufacturer', u8),
        ('iProduct', u8),
        ('iSerialNumber', u8),
        ('bNumConfigurations', u8),
    ]

USB_DT_DEVICE_SIZE = 18
assert ctypes.sizeof(USBDeviceDescriptor) == USB_DT_DEVICE_SIZE

# Device and/or Interface Class codes
# as found in bDeviceClass or bInterfaceClass
# and defined by www.usb.org documents
USB_CLASS_PER_INTERFACE = 0 # for DeviceClass
USB_CLASS_AUDIO = 1
USB_CLASS_COMM = 2
USB_CLASS_HID = 3
USB_CLASS_PHYSICAL = 5
USB_CLASS_STILL_IMAGE = 6
USB_CLASS_PRINTER = 7
USB_CLASS_MASS_STORAGE = 8
USB_CLASS_HUB = 9
USB_CLASS_CDC_DATA = 0x0a
USB_CLASS_CSCID = 0x0b # chip+ smart card
USB_CLASS_CONTENT_SEC = 0x0d # content security
USB_CLASS_VIDEO = 0x0e
USB_CLASS_WIRELESS_CONTROLLER = 0xe0
USB_CLASS_MISC = 0xef
USB_CLASS_APP_SPEC = 0xfe
USB_CLASS_VENDOR_SPEC = 0xff

USB_SUBCLASS_VENDOR_SPEC = 0xff

class USBConfigDescriptor(USBDescriptorHeader):
        """
        USB_DT_CONFIG: Configuration descriptor information.
        """
        _bDescriptorType = USB_DT_CONFIG
        _fields_ = [
            ('wTotalLength', le16),
            ('bNumInterfaces', u8),
            ('bConfigurationValue', u8),
            ('iConfiguration', u8),
            ('bmAttributes', u8),
            ('bMaxPower', u8),
        ]

class USBOtherSpeedConfig(USBConfigDescriptor):
        """
        USB_DT_OTHER_SPEED_CONFIG:  Highspeed-capable devices can look
        different depending on what speed they're currently running.  Only
        devices with a USB_DT_DEVICE_QUALIFIER have any OTHER_SPEED_CONFIG
        descriptors.
        """
        _bDescriptorType = USB_DT_OTHER_SPEED_CONFIG

USB_DT_CONFIG_SIZE = 9
assert ctypes.sizeof(USBOtherSpeedConfig) == USB_DT_CONFIG_SIZE

# from config descriptor bmAttributes
USB_CONFIG_ATT_ONE = (1 << 7) # must be set
USB_CONFIG_ATT_SELFPOWER = (1 << 6) # self powered
USB_CONFIG_ATT_WAKEUP = (1 << 5) # can wakeup
USB_CONFIG_ATT_BATTERY = (1 << 4) # battery powered

class USBStringDescriptor(USBConfigDescriptor):
        """
        USB_DT_STRING: String descriptor

        note that "string" zero is special, it holds language codes that
        the device supports, not Unicode characters.
        """
        _bDescriptorType = USB_DT_STRING
        _fields_ = [
            ('wData', le16), # UTF-16LE encoded
        ]

class USBInterfaceDescriptor(USBDescriptorHeader):
    """
    USB_DT_INTERFACE: Interface descriptor
    """
    _bDescriptorType = USB_DT_INTERFACE
    _fields_ = [
        ('bInterfaceNumber', u8),
        ('bAlternateSetting', u8),
        ('bNumEndpoints', u8),
        ('bInterfaceClass', u8),
        ('bInterfaceSubClass', u8),
        ('bInterfaceProtocol', u8),
        ('iInterface', u8),
    ]

USB_DT_INTERFACE_SIZE = 9
assert ctypes.sizeof(USBInterfaceDescriptor) == USB_DT_INTERFACE_SIZE

# Audio-less variant comes from functionfs.h
class USBEndpointDescriptorNoAudio(USBDescriptorHeader):
    """
    USB_DT_ENDPOINT: Endpoint descriptor without audio fields.
    """
    _bDescriptorType = USB_DT_ENDPOINT
    _fields_ = [
        ('bEndpointAddress', u8),
        ('bmAttributes', u8),
        ('wMaxPacketSize', le16),
        ('bInterval', u8),
    ]

class USBEndpointDescriptor(USBEndpointDescriptorNoAudio):
    """
    USB_DT_ENDPOINT: Endpoint descriptor
    """
    _fields_ = [
        # NOTE:  these two are _only_ in audio endpoints.
        ('bRefresh', u8),
        ('bSynchAddress', u8),
    ]

USB_DT_ENDPOINT_SIZE = 7
USB_DT_ENDPOINT_AUDIO_SIZE = 9 # Audio extension
assert ctypes.sizeof(USBEndpointDescriptorNoAudio) == USB_DT_ENDPOINT_SIZE
assert ctypes.sizeof(USBEndpointDescriptor) == USB_DT_ENDPOINT_AUDIO_SIZE

# Endpoints
USB_ENDPOINT_NUMBER_MASK = 0x0f # in bEndpointAddress
USB_ENDPOINT_DIR_MASK = 0x80

USB_ENDPOINT_XFERTYPE_MASK = 0x03 # in bmAttributes
USB_ENDPOINT_XFER_CONTROL = 0
USB_ENDPOINT_XFER_ISOC = 1
USB_ENDPOINT_XFER_BULK = 2
USB_ENDPOINT_XFER_INT = 3
USB_ENDPOINT_MAX_ADJUSTABLE = 0x80

# The USB 3.0 spec redefines bits 5:4 of bmAttributes as interrupt ep type.
USB_ENDPOINT_INTRTYPE = 0x30
USB_ENDPOINT_INTR_PERIODIC = (0 << 4)
USB_ENDPOINT_INTR_NOTIFICATION = (1 << 4)

USB_ENDPOINT_SYNCTYPE = 0x0c
USB_ENDPOINT_SYNC_NONE = (0 << 2)
USB_ENDPOINT_SYNC_ASYNC = (1 << 2)
USB_ENDPOINT_SYNC_ADAPTIVE = (2 << 2)
USB_ENDPOINT_SYNC_SYNC = (3 << 2)

USB_ENDPOINT_USAGE_MASK = 0x30
USB_ENDPOINT_USAGE_DATA = 0x00
USB_ENDPOINT_USAGE_FEEDBACK = 0x10
USB_ENDPOINT_USAGE_IMPLICIT_FB = 0x20 # Implicit feedback Data endpoint

# To be continued...

# Bonus not in ch9.h:
# Feature selectors from Table 9-8 USB Power Delivery spec
USB_DEVICE_BATTERY_WAKE_MASK = 40
USB_DEVICE_OS_IS_PD_AWARE = 41
USB_DEVICE_POLICY_MODE = 42
USB_PORT_PR_SWAP = 43
USB_PORT_GOTO_MIN = 44
USB_PORT_RETURN_POWER = 45
USB_PORT_ACCEPT_PD_REQUEST = 46
USB_PORT_REJECT_PD_REQUEST = 47
USB_PORT_PORT_PD_RESET = 48
USB_PORT_C_PORT_PD_CHANGE = 49
USB_PORT_CABLE_PD_RESET = 50
USB_DEVICE_CHARGING_POLICY = 54
