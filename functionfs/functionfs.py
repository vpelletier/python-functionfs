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
import ioctl_opt
from .common import u8, le16, le32, Enum
from .ch9 import (
    USBEndpointDescriptor,
    USBCtrlRequest,
)

# Translated from linux/usb/functionfs.h
DESCRIPTORS_MAGIC = 1
STRINGS_MAGIC = 2
DESCRIPTORS_MAGIC_V2 = 3

FLAGS = Enum({
    'HAS_FS_DESC': 1,
    'HAS_HS_DESC': 2,
    'HAS_SS_DESC': 4,
    'HAS_MS_OS_DESC': 8,
    'VIRTUAL_ADDR': 16,
    'EVENTFD': 32,
    'ALL_CTRL_RECIP': 64,
    'CONFIG0_SETUP': 128,
})

# Descriptor of an non-audio endpoint

class DescsHeadV2(ctypes.LittleEndianStructure):
    """
    | off | name      | type         | description                          |
    |-----+-----------+--------------+--------------------------------------|
    |   0 | magic     | LE32         | FUNCTIONFS_DESCRIPTORS_MAGIC_V2      |
    |   4 | length    | LE32         | length of the whole data chunk       |
    |   8 | flags     | LE32         | combination of functionfs_flags      |
    |     | eventfd   | LE32         | eventfd file descriptor              |
    |     | fs_count  | LE32         | number of full-speed descriptors     |
    |     | hs_count  | LE32         | number of high-speed descriptors     |
    |     | ss_count  | LE32         | number of super-speed descriptors    |
    |     | os_count  | LE32         | number of MS OS descriptors          |
    |     | fs_descrs | Descriptor[] | list of full-speed descriptors       |
    |     | hs_descrs | Descriptor[] | list of high-speed descriptors       |
    |     | ss_descrs | Descriptor[] | list of super-speed descriptors      |
    |     | os_descrs | OSDesc[]     | list of MS OS descriptors            |

    Depending on which flags are set, various fields may be missing in the
    structure.  Any flags that are not recognised cause the whole block to be
    rejected with -ENOSYS.
    """
    _pack_ = 1
    _fields_ = [
        ('magic', le32),
        ('length', le32),
        ('flags', le32),
    ]
    # le32 fs_count, hs_count, fs_count; must be included manually in
    # the structure taking flags into consideration.

class DescsHead(ctypes.LittleEndianStructure):
    """
    Legacy descriptors format (deprecated as of 3.14):
    
    | off | name      | type         | description                          |
    |-----+-----------+--------------+--------------------------------------|
    |   0 | magic     | LE32         | FUNCTIONFS_DESCRIPTORS_MAGIC         |
    |   4 | length    | LE32         | length of the whole data chunk       |
    |   8 | fs_count  | LE32         | number of full-speed descriptors     |
    |  12 | hs_count  | LE32         | number of high-speed descriptors     |
    |  16 | fs_descrs | Descriptor[] | list of full-speed descriptors       |
    |     | hs_descrs | Descriptor[] | list of high-speed descriptors       |
    """
    _pack_ = 1
    _fields_ = [
        ('magic', le32),
        ('length', le32),
        ('fs_count', le32),
        ('hs_count', le32),
    ]

class OSDescHeader(ctypes.LittleEndianStructure):
    """
    MS OS Descriptor header

    OSDesc[] is an array of valid MS OS Feature Descriptors which have one of
    the following formats:

    | off | name            | type | description              |
    |-----+-----------------+------+--------------------------|
    |   0 | inteface        | U8   | related interface number |
    |   1 | dwLength        | U32  | length of the descriptor |
    |   5 | bcdVersion      | U16  | currently supported: 1   |
    |   7 | wIndex          | U16  | currently supported: 4   |
    |   9 | bCount          | U8   | number of ext. compat.   |
    |  10 | Reserved        | U8   | 0                        |
    |  11 | ExtCompat[]     |      | list of ext. compat. d.  |

    | off | name            | type | description              |
    |-----+-----------------+------+--------------------------|
    |   0 | inteface        | U8   | related interface number |
    |   1 | dwLength        | U32  | length of the descriptor |
    |   5 | bcdVersion      | U16  | currently supported: 1   |
    |   7 | wIndex          | U16  | currently supported: 5   |
    |   9 | wCount          | U16  | number of ext. compat.   |
    |  11 | ExtProp[]       |      | list of ext. prop. d.    |
    """
    _pack_ = 1
    _anonymous_ = [
        'u',
    ]
    _fields_ = [
        ('interface', u8),
        ('dwLength', le32),
        ('bcdVersion', le16),
        ('wIndex', le16),
        (
            'u',
            type(
                'Count',
                (ctypes.Union, ),
                {
                    '_fields_': [
                        (
                            'b',
                            type(
                                'BCount',
                                (ctypes.LittleEndianStructure, ),
                                {
                                    '_fields_': [
                                        ('bCount', u8),
                                        ('Reserved', u8),
                                    ],
                                }
                            ),
                        ),
                        ('wCount', le16),
                    ],
                },
            ),
        ),
    ]

class OSExt(ctypes.LittleEndianStructure):
    pass

class OSExtCompatDesc(OSExt):
    """
    ExtCompat[] is an array of valid Extended Compatiblity descriptors
    which have the following format:

    | off | name                  | type | description                         |
    |-----+-----------------------+------+-------------------------------------|
    |   0 | bFirstInterfaceNumber | U8   | index of the interface or of the 1st|
    |     |                       |      | interface in an IAD group           |
    |   1 | Reserved              | U8   | 0                                   |
    |   2 | CompatibleID          | U8[8]| compatible ID string                |
    |  10 | SubCompatibleID       | U8[8]| subcompatible ID string             |
    |  18 | Reserved              | U8[6]| 0                                   |
    """
    _fields_ = [
        ('bFirstInterfaceNumber', u8),
        ('Reserved1', u8),
        ('CompatibleID', u8 * 8),
        ('SubCompatibleID', u8 * 8),
        ('Reserved2', u8 * 6),
    ]

class OSExtPropDescHead(OSExt):
    """
    ExtProp[] is an array of valid Extended Properties descriptors
    which have the following format:

    | off | name                  | type | description                         |
    |-----+-----------------------+------+-------------------------------------|
    |   0 | dwSize                | U32  | length of the descriptor            |
    |   4 | dwPropertyDataType    | U32  | 1..7                                |
    |   8 | wPropertyNameLength   | U16  | bPropertyName length (NL)           |
    |  10 | bPropertyName         |U8[NL]| name of this property               |
    |10+NL| dwPropertyDataLength  | U32  | bPropertyData length (DL)           |
    |14+NL| bProperty             |U8[DL]| payload of this property            |
    """
    _pack_ = 1
    _fields_ = [
        ('dwSize', le32),
        ('dwPropertyDataType', le32),
        ('wPropertyNameLength', le16),
    ]

class StringsHead(ctypes.LittleEndianStructure):
    """
    Strings format:

    | off | name       | type                  | description                |
    |-----+------------+-----------------------+----------------------------|
    |   0 | magic      | LE32                  | FUNCTIONFS_STRINGS_MAGIC   |
    |   4 | length     | LE32                  | length of the data chunk   |
    |   8 | str_count  | LE32                  | number of strings          |
    |  12 | lang_count | LE32                  | number of languages        |
    |  16 | stringtab  | StringTab[lang_count] | table of strings per lang  |
    """
    _pack_ = 1
    _fields_ = [
        ('magic', le32),
        ('length', le32),
        ('str_count', le32),
        ('lang_count', le32),
    ]

class StringBase(ctypes.LittleEndianStructure):
    """
    For each language there is one stringtab entry (ie. there are lang_count
    stringtab entires).  Each StringTab has following format:

    | off | name    | type              | description                        |
    |-----+---------+-------------------+------------------------------------|
    |   0 | lang    | LE16              | language code                      |
    |   2 | strings | String[str_count] | array of strings in given language |

    For each string there is one strings entry (ie. there are str_count
    string entries).  Each String is a NUL terminated string encoded in
    UTF-8.
    """
    _pack_ = 1
    _fields_ = [
        ('lang', le16),
    ]


EVENT_TYPE = Enum({
    'BIND': 0,
    'UNBIND': 1,

    'ENABLE': 2,
    'DISABLE': 3,

    'SETUP': 4,

    'SUSPEND': 5,
    'RESUME': 6,
})

class Event(ctypes.LittleEndianStructure):
    """
    Events are delivered on the ep0 file descriptor, when the user mode driver
    reads from this file descriptor after writing the descriptors.  Don't
    stop polling this descriptor.

    NOTE:  this structure must stay the same size and layout on
    both 32-bit and 64-bit kernels.
    """
    _pack_ = 1
    _fields_ = [
        (
            'u',
            type(
                'u',
                (ctypes.Union, ),
                {
                    '_fields_': [
                        # SETUP: packet; DATA phase i/o precedes next event
                        # (setup.bmRequestType & USB_DIR_IN) flags direction
                        ('setup', USBCtrlRequest),
                    ],
                }
            ),
        ),

        # event_type
        ('type', u8),
        ('_pad', u8 * 3),
    ]

# Endpoint ioctls
# The same as in gadgetfs

# IN transfers may be reported to the gadget driver as complete
#    when the fifo is loaded, before the host reads the data;
# OUT transfers may be reported to the host's "client" driver as
#    complete when they're sitting in the FIFO unread.
# THIS returns how many bytes are "unclaimed" in the endpoint fifo
# (needed for precise fault handling, when the hardware allows it)
FIFO_STATUS = ioctl_opt.IO(ord('g'), 1)

# discards any unclaimed data in the fifo.
FIFO_FLUSH = ioctl_opt.IO(ord('g'), 2)

# resets endpoint halt+toggle; used to implement set_interface.
# some hardware (like pxa2xx) can't support this.
CLEAR_HALT = ioctl_opt.IO(ord('g'), 3)

# Specific for functionfs

# Returns reverse mapping of an interface.  Called on EP0.  If there
# is no such interface returns -EDOM.  If function is not active
# returns -ENODEV.
INTERFACE_REVMAP = ioctl_opt.IO(ord('g'), 128)

# Returns real bEndpointAddress of an endpoint.  If function is not
# active returns -ENODEV.
ENDPOINT_REVMAP = ioctl_opt.IO(ord('g'), 129)

# Returns endpoint descriptor. If function is not active returns -ENODEV.
ENDPOINT_DESC = ioctl_opt.IOR(ord('g'), 130, USBEndpointDescriptor)
