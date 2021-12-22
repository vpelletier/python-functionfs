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
from __future__ import print_function
import functools
import socket
import functionfs
import functionfs.ch9
from functionfs.gadget import (
    GadgetSubprocessManager,
    ConfigFunctionFFSSubprocess,
)
from . import common

# Give the hardware an 1MB buffer to send to host.
# Submit the same buffer multiple times, to make better use of hardware
# parallelism without multiplying memory use (we can have up to 15 IN
# endpoints, a real device will likely not be trying to max out the
# bandwidth on all endpoints at the same time, and will not have this
# many endpoints).
# Because as a sanity check every transfer starts with its endpoint
# number, each endpoint must have its own copy of such buffer.
# OUT endpoints cannot use the same trick, as the memory is allocated
# within functionfs (and hence must work for any use-case).
IN_BUFFER_SIZE = 1024 * 1024
IN_TRANSFER_COUNT = 10
OUT_BUFFER_SIZE = 1024 * 1024
OUT_TRANSFER_COUNT = 10

class TestEndpointINFile(functionfs.EndpointINFile):
    def prime(self):
        """
        Called by onEnable to prime the pump.
        """
        buf = bytearray(IN_BUFFER_SIZE)
        buf[0] = self.getDescriptor().bEndpointAddress
        for _ in range(IN_TRANSFER_COUNT):
            self.submit(buffer_list=[buf])

    def onComplete(self, buffer_list, user_data, status):
        # Re-submit buffers.
        # Real code would either modify these buffers here, or submit other
        # buffers.
        if status >= 0:
            assert status == IN_BUFFER_SIZE, status
            return True
        return False

class TestEndpointOUTFile(functionfs.EndpointOUTFile):
    def prime(self):
        """
        Called by onEnable to prime the pump.
        """
        self.__ep_addr = self.getDescriptor().bEndpointAddress

    def onComplete(self, data, status):
        if status == 0:
            # Real code would process the data received from the host here, or
            # copy it and push it to somewhere which will process them.
            if data[0] != self.__ep_addr:
                self.halt()

class FunctionFSTestDevice(functionfs.Function):
    def __init__(self, path, ep_pair_count=15):
        ep_list = [
            functionfs.ch9.USB_DIR_IN,
            functionfs.ch9.USB_DIR_OUT,
        ] * ep_pair_count
        fs_list, hs_list, ss_list = functionfs.getInterfaceInAllSpeeds(
            interface={
                # Interface descriptor properties go here.
                # Non specified properties are set to zero.
                # bNumEndpoints will be automatically filled-in.
                'bInterfaceClass': functionfs.ch9.USB_CLASS_VENDOR_SPEC,
                'iInterface': 1,
            },
            endpoint_list=[
                {
                    # Endpoint descriptors go here.
                    # "endpoint" for the basic endpoint descriptor.
                    # "superspeed" and "superspeed_iso" for its superspeed
                    # companion descriptors, if desired.
                    'endpoint': {
                        'bEndpointAddress': bEndpointAddress,
                        'bmAttributes': functionfs.ch9.USB_ENDPOINT_XFER_BULK,
                    },
                }
                for bEndpointAddress in ep_list
            ],
            # Could also provide a list of class-specific descriptors.
        )
        super().__init__(
            path,
            fs_list=fs_list,
            hs_list=hs_list,
            ss_list=ss_list,
            lang_dict={
                0x0409: [
                    common.INTERFACE_NAME,
                ],
            },
            # With SuperSpeed descriptors generated with default bulk
            # wMaxPacketSize (1024B), this means ten 1MB buffers per OUT
            # endpoint, so up to 150MB with the maximum 15 endpoints.
            out_aio_blocks_per_endpoint=OUT_TRANSFER_COUNT,
            out_aio_blocks_max_packet_count=OUT_BUFFER_SIZE // 1024,
        )
        self.__ep_count = len(ep_list)
        self.__echo_payload = b'NOT SET'

    def getEndpointClass(self, is_in, descriptor):
        return TestEndpointINFile if is_in else TestEndpointOUTFile

    def onEnable(self):
        print('functionfs: onEnable')
        super().onEnable()
        try:
            print('Real interface 0:', self.ep0.getRealInterfaceNumber(0))
        except IOError:
            pass
        for ep_file in self._ep_list[1:]:
            print(ep_file.name + ':')
            descriptor = ep_file.getDescriptor()
            for klass in reversed(descriptor.__class__.mro()):
                for arg_id, _ in getattr(klass, '_fields_', ()):
                    print('  %s\t%s' % (
                        {
                            'b': '  0x%02x',
                            'w': '0x%04x',
                        }[arg_id[0]] % (getattr(descriptor, arg_id), ),
                        arg_id,
                    ))
            print('  FIFO status:', end='')
            try:
                value = ep_file.getFIFOStatus()
            except IOError as exc:
                print('(failed: %r)' % (exc, ))
            else:
                print(value)
            print('  Real number:', ep_file.getRealEndpointNumber())
            # XXX: can this raise if endpoint is not halted ?
            ep_file.clearHalt()
            ep_file.flushFIFO()
            ep_file.prime()

    def onDisable(self):
        print('functionfs: onDisable')
        super().onDisable()

    def onBind(self):
        print('functionfs: onBind')
        super().onBind()

    def onUnbind(self):
        print('functionfs: onUnbind')
        super().onUnbind()

    def onSuspend(self):
        print('functionfs: onSuspend')
        super().onSuspend()

    def onResume(self):
        print('functionfs: onResume')
        super().onResume()

    def disableRemoteWakeup(self):
        print('functionfs: disableRemoteWakeup')
        super().disableRemoteWakeup()

    def enableRemoteWakeup(self):
        print('functionfs: enableRemoteWakeup')
        super().enableRemoteWakeup()

    def onSetup(self, request_type, request, value, index, length):
        print(
            'functionfs: onSetup(request_type=%#04x, request=%#04x, '
            'value=%#06x, index=%#06x, length=%#06x)' % (
                request_type,
                request,
                value,
                index,
                length,
            ),
        )
        request_type_type = request_type & functionfs.ch9.USB_TYPE_MASK
        if request_type_type == functionfs.ch9.USB_TYPE_VENDOR:
            if request == common.REQUEST_ECHO:
                if (request_type & functionfs.ch9.USB_DIR_IN) == functionfs.ch9.USB_DIR_IN:
                    self.ep0.write(self.__echo_payload[:length])
                elif length:
                    self.__echo_payload = self.ep0.read(length)
            else:
                print('functionfs: onSetup: halt')
                self.ep0.halt(request_type)
        else:
            super().onSetup(
                request_type, request, value, index, length,
            )

def main():
    parser = GadgetSubprocessManager.getArgumentParser(
        description='python-functionfs test gadget',
    )
    parser.add_argument(
        '--ep-count',
        type=int,
        default=15,
        help='Number of pairs (IN + OUT) of USB endpoints to request from '
        'UDC. Each endpoint pair needs %iMB of RAM for transfer buffers.' % (
            (
                IN_BUFFER_SIZE + OUT_BUFFER_SIZE * OUT_TRANSFER_COUNT
            ) / (
                1024 * 1024
            ),
        ),
    )
    args = parser.parse_args()
    with GadgetSubprocessManager(
        args=args,
        config_list=[
            {
                'function_list': [
                    functools.partial(
                        ConfigFunctionFFSSubprocess,
                        getFunction=functools.partial(
                            FunctionFSTestDevice,
                            ep_pair_count=args.ep_count,
                        ),
                    ),
                ],
                'MaxPower': 500,
                'lang_dict': {
                    0x409: {
                        'configuration': 'test',
                    },
                },
            }
        ],
        idVendor=0x1d6b, # Linux Foundation
        idProduct=0x0104, # Multifunction Composite Gadget
        lang_dict={
            0x409: {
                'serialnumber': '1234',
                'product': socket.gethostname(),
                'manufacturer': 'Foo Corp.',
            },
        },
    ) as gadget:
        # Note: events are not serviced in this process, but in the process
        # spawned by ConfigFunctionFFSSubprocess.
        print('Servicing functionfs events forever...')
        gadget.waitForever()

if __name__ == '__main__':
    main()
