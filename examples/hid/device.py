#!/usr/bin/env python
# This file is part of python-functionfs
# Copyright (C) 2018-2020  Vincent Pelletier <plr.vincent@gmail.com>
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
Illustration of how to use functionfs to define an HID USB device.
"""
from __future__ import print_function
import argparse
import errno
import functools
import pwd
import signal
import functionfs
from functionfs.gadget import Gadget, SubprocessFunction

# This is the exact HID mouse descriptor as present in the HID 1.11
# specification, Appendix E.10
REPORT_DESCRIPTOR = (
    b'\x05\x01\x09\x02\xa1\x01\x09\x01'
    b'\xa1\x00\x05\x09\x19\x01\x29\x03'
    b'\x15\x00\x25\x01\x95\x03\x75\x01'
    b'\x81\x02\x95\x01\x75\x05\x81\x01'
    b'\x05\x01\x09\x30\x09\x31\x15\x81'
    b'\x25\x7f\x75\x08\x95\x02\x81\x06'
    b'\xc0\xc0'
)

# No report number, no button pressed, +1 on X, no movement on Y.
GO_RIGHT_REPORT = bytearray(b'\x00\x01\x00')

class HIDINEndpoint(functionfs.EndpointINFile):
    """
    Customise what happens on IN transfer completion.
    In a real device, here may be where you would sample and clear the current
    movement deltas, and construct a new HID report to send to the host.
    """
    def onComplete(self, buffer_list, user_data, status):
        if status < 0:
            if status == -errno.ESHUTDOWN:
                # Mouse is unplugged, host selected another configuration, ...
                # Stop submitting the transfer.
                return False
            raise IOError(-status)
        # Resubmit the transfer. We did not change its buffer, so the
        # mouse movement will carry on identically.
        return True

class Mouse(functionfs.HIDFunction):
    """
    A simple mouse device.
    """
    def getEndpointClass(self, is_in, descriptor):
        """
        Tall HIDFunction that we want it to use our custom IN endpoint class
        for our only IN endpoint.
        """
        if is_in:
            return HIDINEndpoint
        return super(Mouse, self).getEndpointClass(is_in, descriptor)

    def getHIDReport(self, value, index, length):
        """
        In case the host does not read our IN endpoint but instead uses the
        control endpoint to request reports.
        """
        self.ep0.write(GO_RIGHT_REPORT)

    def onEnable(self):
        """
        We are plugged to a host, it has enumerated and enabled us, start
        sending reports.
        """
        print('onEnable called')
        super(Mouse, self).onEnable()
        self.getEndpoint(1).submit(
            (GO_RIGHT_REPORT, ),
        )

class SubprocessMouse(SubprocessFunction):
    """
    Just to illustrate subclassing SubprocessFunction.
    """
    def run(self):
        print('Function ready, processing USB events.')
        super(SubprocessMouse, self).run()
        print('Function exiting normaly.')

def main():
    """
    Entry point.
    """
    parser = argparse.ArgumentParser(
        description='Example implementation of an USB HID gadget emulating a '
        'mouse moving right.',
        epilog='Requires CAP_SYS_ADMIN in order to mount the required '
        'functionfs filesystem, and libcomposite kernel module to be '
        'loaded (or built-in).',
    )
    parser.add_argument(
        '--udc',
        help='Name of the UDC to use (default: autodetect)',
    )
    parser.add_argument(
        '--username',
        help='Run function under this user. For improved security.',
    )
    args = parser.parse_args()
    if args.username is None:
        uid = gid = None
    else:
        passwd = pwd.getpwnam(args.username)
        uid = passwd.pw_uid
        gid = passwd.pw_gid
    def raiseKeyboardInterrupt(signal_number, stack_frame):
        """
        Make gadget exit if function subprocess exits.
        """
        _ = signal_number # Silence pylint
        _ = stack_frame # Silence pylint
        raise KeyboardInterrupt
    with Gadget(
        udc=args.udc,
        config_list=[
            # A single configuration
            {
                'function_list': [
                    { # A single function
                        'function': SubprocessMouse(
                            getFunction=functools.partial(
                                Mouse,
                                report_descriptor=REPORT_DESCRIPTOR,
                                in_report_max_length=len(GO_RIGHT_REPORT),
                            ),
                            uid=uid,
                            gid=gid,
                        ),
                        'mount': {
                            'uid': uid,
                            'gid': gid,
                        },
                    },
                ],
                'MaxPower': 500,
                'lang_dict': {
                    0x409: {
                        'configuration': 'mouse demo function',
                    },
                },
            }
        ],
        idVendor=0x1d6b, # Linux Foundation
        idProduct=0x0104, # Multifunction Composite Gadget
        lang_dict={
            0x409: {
                'product': 'HID mouse demo',
                'manufacturer': 'python-functionfs',
            },
        },
    ):
        signal.signal(signal.SIGCHLD, raiseKeyboardInterrupt)
        print('Gadget ready, waiting for function to exit.')
        try:
            while True:
                signal.pause()
        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        print('Gadget exiting.')

if __name__ == '__main__':
    main()
