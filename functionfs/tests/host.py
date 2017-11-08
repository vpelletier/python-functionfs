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
from __future__ import print_function

from time import time
import usb1
from . import common

def main():
    with usb1.USBContext() as context:
        context.setDebug(usb1.LOG_LEVEL_DEBUG)
        handle = context.openByVendorIDAndProductID(
            0x1d6b,
            0x0104,
            skip_on_error=True,
        )
        if handle is None:
            print('Device not found')
            return
        device = handle.getDevice()
        assert len(device) == 1
        configuration = device[0]
        assert len(configuration) == 1
        interface = configuration[0]
        assert len(interface) == 1
        alt_setting = interface[0]
        lang_id, = handle.getSupportedLanguageList()
        interface_name = handle.getStringDescriptor(alt_setting.getDescriptor(), lang_id)
        interface_name_ascii = handle.getASCIIStringDescriptor(alt_setting.getDescriptor())
        assert interface_name == common.INTERFACE_NAME == interface_name_ascii, (repr(interface_name), repr(interface_name_ascii))

        try:
            handle.controlRead(
                usb1.TYPE_VENDOR | usb1.RECIPIENT_INTERFACE,
                common.REQUEST_STALL,
                0,
                0,
                1,
            )
        except usb1.USBErrorPipe:
            pass
        else:
            raise ValueError('Did not stall')

        try:
            handle.controlWrite(
                usb1.TYPE_VENDOR | usb1.RECIPIENT_INTERFACE,
                common.REQUEST_STALL,
                0,
                0,
                'a',
            )
        except usb1.USBErrorPipe:
            pass
        else:
            raise ValueError('Did not stall')

        echo_value = None
        for length in range(1, 65):
            echo_next_value = handle.controlRead(
                usb1.TYPE_VENDOR | usb1.RECIPIENT_INTERFACE,
                common.REQUEST_ECHO,
                0,
                0,
                length,
            )
            if echo_next_value == echo_value:
                break
            print(repr(echo_next_value))
            echo_value = echo_next_value
        handle.controlWrite(
            usb1.TYPE_VENDOR | usb1.RECIPIENT_INTERFACE,
            common.REQUEST_ECHO,
            0,
            0,
            'foo bar baz',
        )
        print(repr(handle.controlRead(
            usb1.TYPE_VENDOR | usb1.RECIPIENT_INTERFACE,
            common.REQUEST_ECHO,
            0,
            0,
            64,
        )))

        size = [0]
        def onTransfer(transfer):
            result = time() < deadline
            if result:
                size[0] += transfer.getActualLength()
            return result

        usb_file_data_reader = usb1.USBTransferHelper()
        usb_file_data_reader.setEventCallback(
            usb1.TRANSFER_COMPLETED,
            onTransfer,
        )
        NUM_TRANSFER = 8
        transfer_list = [handle.getTransfer() for _ in range(NUM_TRANSFER)]

        active_configuration = handle.getConfiguration()
        if active_configuration != 1:
            print('Unexpected active configuration:', active_configuration)
            handle.setConfiguration(1)
            active_configuration = handle.getConfiguration()
            assert active_configuration == 1, active_configuration
        handle.claimInterface(0)
        DURATION = .2
        buf = bytearray(512)
        for ep_desc in alt_setting:
            ep = ep_desc.getAddress()
            if ep & 0xf0:
                buf[0] = 0
            else:
                for offset in range(len(buf)):
                    buf[offset] = ep
            size[0] = 0
            for transfer in transfer_list:
                transfer.setBulk(
                    ep,
                    buf,
                    callback=usb_file_data_reader,
                    timeout=int(DURATION * 1000),
                )
                transfer.submit()
            begin = time()
            deadline = begin + DURATION
            while any(x.isSubmitted() for x in transfer_list):
                context.handleEvents()
            actual_duration = time() - begin
            print('%i%s' % (
                ep & 0x7f,
                'IN' if ep & 0x80 else 'OUT',
            ), '\tbandwidth: %i B/s (%.2fs)' % (size[0] / actual_duration, actual_duration), hex(buf[0]))

if __name__ == '__main__':
    main()
