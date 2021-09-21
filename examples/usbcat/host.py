#!/usr/bin/env python -u
# This file is part of python-functionfs
# Copyright (C) 2016-2021  Vincent Pelletier <plr.vincent@gmail.com>
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

import fcntl
import os
import select
import sys
import usb1

PENDING_READ_COUNT = 2
BUF_SIZE = 1024 * 1024

def main():
    with usb1.USBContext() as context:
        for device in context.getDeviceIterator(skip_on_error=True):
            try:
                handle = device.open()
            except usb1.USBErrorAccess:
                continue
            for interface in device[handle.getConfiguration() - 1]:
                if len(interface) != 1:
                    continue
                interface_setting = interface[0]
                if (
                    interface_setting.getNumEndpoints() == 2 and
                    interface_setting.getClass() == usb1.CLASS_VENDOR_SPEC and
                    handle.getStringDescriptor(
                        interface_setting.getDescriptor(),
                        0x0409,
                    ) == 'USBCat'
                ):
                    interface_number = interface_setting.getNumber()
                    print('Device found at %03i:%03i interface %i' % (
                        device.getBusNumber(),
                        device.getDeviceAddress(),
                        interface_number,
                    ))
                    handle.claimInterface(interface_number)
                    to_device, = [
                        x.getAddress()
                        for x in interface_setting
                        if x.getAddress() & usb1.ENDPOINT_DIR_MASK == usb1.ENDPOINT_OUT
                    ]
                    from_device, = [
                        x.getAddress()
                        for x in interface_setting
                        if x.getAddress() & usb1.ENDPOINT_DIR_MASK == usb1.ENDPOINT_IN
                    ]
                    break
            else:
                continue
            break
        else:
            print('Device not found')
            return
        fcntl.fcntl(
            sys.stdin,
            fcntl.F_SETFL,
            fcntl.fcntl(sys.stdin, fcntl.F_GETFL) | os.O_NONBLOCK,
        )
        def sender():
            buf = sys.stdin.read(BUF_SIZE)
            if not buf:
                raise EOFError
            print('sending', len(buf), 'bytes', file=sys.stderr)
            handle.bulkWrite(to_device, buf)
        def onReceive(transfer):
            length = transfer.getActualLength()
            print('received', length, 'bytes', file=sys.stderr)
            sys.stdout.write(transfer.getBuffer()[:length])
            return True
        transfer_helper = usb1.USBTransferHelper()
        transfer_helper.setEventCallback(usb1.TRANSFER_COMPLETED, onReceive)
        transfer_list = []
        for _ in range(PENDING_READ_COUNT):
            transfer = handle.getTransfer()
            transfer.setBulk(from_device, BUF_SIZE, transfer_helper)
            transfer.submit()
            transfer_list.append(transfer)
        epoll = usb1.USBPoller(context, select.epoll())
        event_dispatcher_dict = {}
        def register(file_object, handler):
            epoll.register(file_object, select.EPOLLIN)
            event_dispatcher_dict[file_object.fileno()] = handler
        register(sys.stdin, sender)
        try:
            while True:
                for fd, event in epoll.poll(10):
                    print(
                        'epoll: fd %r got event %r' % (fd, event),
                        file=sys.stderr,
                    )
                    event_dispatcher_dict[fd]()
        except (KeyboardInterrupt, EOFError):
            pass

if __name__ == '__main__':
    main()
