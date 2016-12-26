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
import sys

class Enum(object):
    def __init__(self, member_dict, scope_dict=None):
        if scope_dict is None:
            # Affect caller's locals, not this module's.
            # pylint: disable=protected-access
            scope_dict = sys._getframe(1).f_locals
            # pylint: enable=protected-access
        forward_dict = {}
        reverse_dict = {}
        next_value = 0
        for name, value in member_dict.items():
            if value is None:
                value = next_value
                next_value += 1
            forward_dict[name] = value
            if value in reverse_dict:
                raise ValueError('Multiple names for value %r: %r, %r' % (
                    value, reverse_dict[value], name
                ))
            reverse_dict[value] = name
            scope_dict[name] = value
        self.forward_dict = forward_dict
        self.reverse_dict = reverse_dict

    def __call__(self, value):
        return self.reverse_dict[value]

    def get(self, value, default=None):
        return self.reverse_dict.get(value, default)

u8 = ctypes.c_ubyte
assert ctypes.sizeof(u8) == 1
le16 = ctypes.c_ushort
assert ctypes.sizeof(le16) == 2
le32 = ctypes.c_uint
assert ctypes.sizeof(le32) == 4

class USBDescriptorHeader(ctypes.LittleEndianStructure):
    """
    All standard descriptors have these 2 fields at the beginning
    """
    _pack_ = 1
    _fields_ = [
        ('bLength', u8),
        ('bDescriptorType', u8),
    ]
