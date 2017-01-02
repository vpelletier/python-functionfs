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
import errno
import sys
import threading
import functionfs
import functionfs.ch9
from . import common

FS_BULK_MAX_PACKET_SIZE = 64
HS_BULK_MAX_PACKET_SIZE = 512

INTERFACE_DESCRIPTOR = functionfs.getDescriptor(
    functionfs.USBInterfaceDescriptor,
    bInterfaceNumber=0,
    bAlternateSetting=0,
    bNumEndpoints=2, # bulk-IN, bulk-OUT
    bInterfaceClass=functionfs.ch9.USB_CLASS_VENDOR_SPEC,
    bInterfaceSubClass=0,
    bInterfaceProtocol=0,
    iInterface=1,
)

class FunctionFSTestDevice(functionfs.Function):
    def __init__(self, path):
        super(FunctionFSTestDevice, self).__init__(
            path,
            fs_list=(
                INTERFACE_DESCRIPTOR,
                functionfs.getDescriptor(
                    functionfs.USBEndpointDescriptorNoAudio,
                    bEndpointAddress=1 | functionfs.ch9.USB_DIR_OUT,
                    bmAttributes=functionfs.ch9.USB_ENDPOINT_XFER_BULK,
                    wMaxPacketSize=FS_BULK_MAX_PACKET_SIZE,
                    bInterval=0,
                ),
                functionfs.getDescriptor(
                    functionfs.USBEndpointDescriptorNoAudio,
                    bEndpointAddress=2 | functionfs.ch9.USB_DIR_IN,
                    bmAttributes=functionfs.ch9.USB_ENDPOINT_XFER_BULK,
                    wMaxPacketSize=FS_BULK_MAX_PACKET_SIZE,
                    bInterval=0,
                ),
            ),
            hs_list=(
                INTERFACE_DESCRIPTOR,
                functionfs.getDescriptor(
                    functionfs.USBEndpointDescriptorNoAudio,
                    bEndpointAddress=1 | functionfs.ch9.USB_DIR_OUT,
                    bmAttributes=functionfs.ch9.USB_ENDPOINT_XFER_BULK,
                    wMaxPacketSize=HS_BULK_MAX_PACKET_SIZE,
                    bInterval=0,
                ),
                functionfs.getDescriptor(
                    functionfs.USBEndpointDescriptorNoAudio,
                    bEndpointAddress=2 | functionfs.ch9.USB_DIR_IN,
                    bmAttributes=functionfs.ch9.USB_ENDPOINT_XFER_BULK,
                    wMaxPacketSize=HS_BULK_MAX_PACKET_SIZE,
                    bInterval=0,
                ),
            ),
#            ss_list=DESC_LIST,
            lang_dict={
                0x0409: [x.decode('utf-8') for x in (
                    common.INTERFACE_NAME,
                )],
            },
        )
        self.__echo_payload = 'NOT SET'

    def onEnable(self):
        print 'functionfs: ENABLE'

    def onDisable(self):
        print 'functionfs: DISABLE'

    def onBind(self):
        print 'functionfs: BIND'

    def onUnbind(self):
        print 'functionfs: UNBIND'

    def onSuspend(self):
        print 'functionfs: SUSPEND'

    def onResume(self):
        print 'functionfs: RESUME'

    def onSetup(self, request_type, request, value, index, length):
        request_type_type = request_type & functionfs.ch9.USB_TYPE_MASK
        if request_type_type == functionfs.ch9.USB_TYPE_VENDOR:
            if request == common.REQUEST_ECHO:
                if (request_type & functionfs.ch9.USB_DIR_IN) == functionfs.ch9.USB_DIR_IN:
                    self.ep0.write(self.__echo_payload[:length])
                elif length:
                    self.__echo_payload = self.ep0.read(length)
            else:
                print 'functionfs: onSetup: halt'
                self.ep0.halt(request_type)
        else:
            super(FunctionFSTestDevice, self).onSetup(
                request_type, request, value, index, length,
            )

def main(path):
    with FunctionFSTestDevice(path) as function:
        echo_buf = bytearray(0x10000)
        def writer():
            ep = function.getEndpoint(2)
            while True:
                try:
                    ep.write(echo_buf)
                except IOError, exc:
                    if exc.errno not in (errno.EINTR, errno.EAGAIN):
                        raise
        def reader():
            ep = function.getEndpoint(1)
            while True:
                try:
                    ep.readinto(echo_buf)
                except IOError, exc:
                    if exc.errno not in (errno.EINTR, errno.EAGAIN):
                        raise
        # XXX: {write,read}thread untested (DWC3 bug on 4.9/4.10 ?)
        writethread = threading.Thread(target=writer)
        writethread.daemon = True
        writethread.start()
        readthread = threading.Thread(target=reader)
        readthread.daemon = True
        readthread.start()
        print 'Servicing functionfs events forever...'
        function.processEventsForever()

if __name__ == '__main__':
    main(*sys.argv[1:])
