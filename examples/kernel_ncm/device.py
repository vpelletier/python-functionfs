#!/usr/bin/env python
# This file is part of python-functionfs
# Copyright (C) 2021  Vincent Pelletier <plr.vincent@gmail.com>
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
import json
import random
import signal
import struct
from functionfs.gadget import (
    Gadget,
    ConfigFunctionKernel,
)

DERIVE_MAC = object()
_SYS_CLASS_NET = '/sys/class/net'

class ConfigFunctionCDCNCM(ConfigFunctionKernel):
    """
    Communications Device Class - Network Control Model

    Note: this example is way overkill. The kernel provides (supposedly) sane
    defaults, but I wanted to play a bit with MAC addresses.
    """
    type_name = 'ncm'

    @staticmethod
    def bytes2mac(mac):
        """
        Convert a MAC address from a 6-bytes object into a colon-separated
        representation.
        """
        return ':'.join('%02x' % (x, ) for x in struct.unpack('B' * 6, mac))

    @staticmethod
    def mac2bytes(mac):
        """
        Convert a MAC address from a colon-separated representation into a
        6-bytes object.
        """
        return struct.pack('B' * 6, *(int(x, 16) for x in mac.split(':')))

    def __init__(
        self,
        ifname=None,
        qmult=None,
        host_addr=DERIVE_MAC,
        dev_addr=DERIVE_MAC,
        name=None,
    ):
        """
        ifname (str, None)
            Name template of the network interface for the device end of the
            link. Must contain exactly one "%d" field.
        qmult (int)
            queue length multiplier for high- and super-speed
        host_addr (str, DERIVE_MAC, None)
            MAC address of host's end of this Ethernet over USB link,
            colon-separated.
            If DERIVE_MAC, a locally-managed random MAC will be generated from
            the OUI of any universaly-managed link address found on this
            system.
        dev_addr (str, DERIVE_MAC, None)
            MAC address of device's end of this Ethernet over USB link,
            colon-separated.
            If DERIVE_MAC, a locally-managed random MAC will be generated from
            the OUI of any universaly-managed link address found on this
            system.
        name (str, None)
            Name of this gadget function.
        """
        if DERIVE_MAC in (host_addr, dev_addr):
            # Find a universally administered MAC address and derive a random
            # locally administered address from it.
            mac_set = set()
            for netdev in os.listdir(_SYS_CLASS_NET):
                with open(os.path.join(
                    _SYS_CLASS_NET,
                    netdev,
                    'address',
                )) as netdev_address:
                    mac_set.add(self.mac2bytes(netdev_address.read().strip()))
            mac_set.update((
                self.mac2bytes(x)
                for x in (host_addr, dev_addr)
                if x not in (None, DERIVE_MAC)
            ))
            oui = None
            for mac in mac_set:
                msB, = struct.unpack('B', mac[0:1])
                # bit0 is multicast, should never be set on an interface's MAC,
                # but check it as it's free.
                # bit1 is address administration scope: admin (who decided to
                # use this USB function) did not tell us which address to pick,
                # so pick the first universally-managed address we find and
                # derive its locally-managed for our own use.
                if msB & 0x03 == 0:
                    oui = struct.pack('B', msB | 0x02) + mac[1:3]
                    break
            if oui is None:
                raise ValueError('Cannot find a universal MAC to derive from')
            def getMAC():
                """
                Get a random locally-managed and locally unique MAC.
                """
                for _ in range(10):
                    mac = oui + struct.pack(
                        'BBB', 
                        random.getrandbits(8),
                        random.getrandbits(8),
                        random.getrandbits(8),
                    )
                    if mac not in mac_set:
                        break
                else:
                    raise ValueError('Could not find a free mac, giving up')
                mac_set.add(mac)
                return self.bytes2mac(mac)
            if host_addr is DERIVE_MAC:
                host_addr = getMAC()
            if dev_addr is DERIVE_MAC:
                dev_addr = getMAC()
        config_dict = {}
        if host_addr is not None:
            config_dict['host_addr'] = host_addr
        if dev_addr is not None:
            config_dict['dev_addr'] = dev_addr
        if qmult is not None:
            config_dict['qmult'] = '%i' % qmult
        if ifname is not None:
            config_dict['ifname'] = ifname
        super(ConfigFunctionCDCNCM, self).__init__(
            config_dict=config_dict,
            name=name,
        )

def main():
    parser = argparse.ArgumentParser(
        description='python-functionfs example with the kernel CDC-NCM '
        'function',
        epilog='Requires CAP_SYS_ADMIN in order to mount the required '
        'functionfs filesystem, and libcomposite kernel module to be '
        'loaded (or built-in).',
    )
    parser.add_argument(
        '--udc',
        help='Name of the UDC to use (default: autodetect)',
    )
    args = parser.parse_args()
    with Gadget(
        udc=args.udc,
        config_list=[
            {
                'function_list': [
                    ConfigFunctionCDCNCM(),
                ],
                'MaxPower': 500,
            },
        ],
        idVendor=0x1d6b, # Linux Foundation
        idProduct=0x0104, # Multifunction Composite Gadget
    ) as gadget:
        try:
            while True:
                signal.pause()
        except KeyboardInterrupt:
            pass

if __name__ == '__main__':
    main()
