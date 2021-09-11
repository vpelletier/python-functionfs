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
import argparse
from collections import OrderedDict
import os.path
import signal
from functionfs.gadget import (
    Gadget,
    ConfigFunctionKernel,
)

class SourceSinkFunction(ConfigFunctionKernel):
    type_name = 'SourceSink'

def main():
    parser = argparse.ArgumentParser(
        description='python-functionfs example with the kernel SourceSink '
        'function',
        epilog='Requires CAP_SYS_ADMIN in order to mount the required '
        'functionfs filesystem, and libcomposite kernel module to be '
        'loaded (or built-in).',
    )
    parser.add_argument(
        '--udc',
        help='Name of the UDC to use (default: autodetect)',
    )
    parser.add_argument(
        'sourcesink',
        metavar='PATH=VALUE',
        nargs='*',
        help='SourceSink parameters. VALUE is written to PATH (relative to '
        'the function) before the gadget gets attached to the bus.',
    )
    args = parser.parse_args()
    sourcesink_dict = OrderedDict()
    for sourcesink in args.sourcesink:
        key, value = sourcesink.split('=', 1)
        sourcesink_dict[os.path.normpath(key)] = value
    with Gadget(
        udc=args.udc,
        config_list=[
            {
                'function_list': [
                    SourceSinkFunction(config_dict=sourcesink_dict),
                ],
                'MaxPower': 500,
                'lang_dict': {
                    0x409: {
                        'configuration': 'SourceSink demo function',
                    },
                },
            }
        ],
        idVendor=0x1d6b, # Linux Foundation
        idProduct=0x0104, # Multifunction Composite Gadget
        lang_dict={
            0x409: {
                'product': 'SourceSink demo',
                'manufacturer': 'python-functionfs',
            },
        },
    ) as gadget:
        try:
            signal.pause()
        except KeyboardInterrupt:
            pass

if __name__ == '__main__':
    main()
