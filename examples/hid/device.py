#!/usr/bin/env python
# This file is part of python-functionfs
# Copyright (C) 2018  Vincent Pelletier <plr.vincent@gmail.com>
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
import errno
import sys
import functionfs

# No report number
# No button pressed, +1 on X, no movement on Y.
DUMMY_REPORT = bytearray(b'\x00\x01\x00')

class Mouse(functionfs.HIDFunction):
    def getHIDReport(self, value, index, length):
        self.ep0.write(DUMMY_REPORT)

    def onEnable(self):
        super(Mouse, self).onEnable()
        # Prime the pump
        self.submitIN(
            1,
            (DUMMY_REPORT, ),
        )

    def onINComplete(self, endpoint_index, buffer_list, user_data, status):
        if status < 0:
            if status == -errno.ESHUTDOWN:
                return False
            raise IOError(-status)
        return True

def main(path):
    with Mouse(
        path=path,
        # HID 1.11, Appendix E.10
        report_descriptor=b''
            b'\x05\x01'
            b'\x09\x02'
            b'\xa1\x01'
            b'\x09\x01'
            b'\xa1\x00'
            b'\x05\x09'
            b'\x19\x01'
            b'\x29\x03'
            b'\x15\x00'
            b'\x25\x01'
            b'\x95\x03'
            b'\x75\x01'
            b'\x81\x02'
            b'\x95\x01'
            b'\x75\x05'
            b'\x81\x01'
            b'\x05\x01'
            b'\x09\x30'
            b'\x09\x31'
            b'\x15\x81'
            b'\x25\x7f'
            b'\x75\x08'
            b'\x95\x02'
            b'\x81\x06'
            b'\xc0'
            b'\xc0',
        in_report_max_length=3,
    ) as function:
        try:
            function.processEventsForever()
        except KeyboardInterrupt:
            pass

if __name__ == '__main__':
    main(*sys.argv[1:])
