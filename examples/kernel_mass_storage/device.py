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
import os.path
import signal
from functionfs.gadget import (
    Gadget,
    ConfigFunctionKernel,
)

class MassStorageFunction(ConfigFunctionKernel):
    type_name = 'mass_storage'

    def __init__(self, lun_list, name=None):
        self._lun_list = lun_list
        self._lun_dir_list = []
        super().__init__(
            config_dict={'stall': '1'},
            name=name,
        )

    def start(self, path):
        lun_dir_list = self._lun_dir_list
        for index, lun in enumerate(self._lun_list):
            lun_dir = os.path.join(path, 'lun.%i' % index)
            if not os.path.exists(lun_dir):
                os.mkdir(lun_dir)
                lun_dir_list.append(lun_dir)
            with open(os.path.join(lun_dir, 'file'), 'w') as lun_file:
                lun_file.write(lun)
        super().start(path)

    def kill(self):
        for lun_path in self._lun_dir_list:
            os.rmdir(lun_path)
        # In the off-chance that it does something someday.
        super().kill()

def main():
    parser = argparse.ArgumentParser(
        description='python-functionfs example with the kernel mass storage '
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
        'lun',
        nargs='+',
        help='Files (typically: block devices) to expose to host as '
        'disks. WARNING: host gets full write (and read) access to these '
        'files, and is going to expect some partition table and filesystem.',
    )
    args = parser.parse_args()
    with Gadget(
        udc=args.udc,
        config_list=[
            {
                'function_list': [
                    MassStorageFunction(lun_list=args.lun),
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
    ):
        try:
            signal.pause()
        except KeyboardInterrupt:
            pass

if __name__ == '__main__':
    main()
