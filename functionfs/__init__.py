# This file is part of python-functionfs
# Copyright (C) 2016-2019  Vincent Pelletier <plr.vincent@gmail.com>
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
Interfaces with functionfs to simplify USB gadget function declaration and
implementation on linux.

Defines standard USB descriptors (see "ch9" submodule) and sends them to the
kernel to declare function's structure.
Provides methods for accessing each endpoint and to react to events.
"""
import ctypes
import errno
import fcntl
import io
import itertools
import math
import os
import struct
import warnings
from .common import (
    USBDescriptorHeader,
    le32,
)
from . import ch9
from .ch9 import (
    USBInterfaceDescriptor,
    USBEndpointDescriptorNoAudio,
    USBEndpointDescriptor,
    USBSSEPCompDescriptor,
    # USBSSPIsocEndpointDescriptor is not implemented in kernel as of this
    # writing.
    USBSSPIsocEndpointDescriptor,
    # USBQualifierDescriptor is reserved for gadgets, so don't expose it.
    USBOTGDescriptor,
    USBOTG20Descriptor,
    # USBDebugDescriptor is not implemented in kernelas of this writing.
    USBDebugDescriptor,
    USBInterfaceAssocDescriptor,
)
from .functionfs import (
    DESCRIPTORS_MAGIC, STRINGS_MAGIC, DESCRIPTORS_MAGIC_V2,
    FLAGS,
    DescsHeadV2,
    DescsHead,
    OSDescHeader,
    OSDescHeaderBCount,
    OSExtCompatDesc,
    OSExtPropDescHead,
    StringsHead,
    StringBase,
    Event,
    FIFO_STATUS, FIFO_FLUSH, CLEAR_HALT, INTERFACE_REVMAP, ENDPOINT_REVMAP, ENDPOINT_DESC,
)
# pylint: disable=no-name-in-module
from .functionfs import (
    HAS_FS_DESC,
    HAS_HS_DESC,
    HAS_SS_DESC,
    HAS_MS_OS_DESC,
    ALL_CTRL_RECIP,
    CONFIG0_SETUP,
    BIND, UNBIND, ENABLE, DISABLE, SETUP, SUSPEND, RESUME,
)
# pylint: enable=no-name-in-module

__all__ = (
    'ch9',
    'Function',

    # XXX: Not very pythonic...
    'getInterfaceInAllSpeeds',
    'getDescriptor',
    'getOSDesc',
    'getOSExtPropDesc',
    'USBInterfaceDescriptor',
    'USBEndpointDescriptorNoAudio',
    'USBEndpointDescriptor',
    'USBSSEPCompDescriptor',
    'USBSSPIsocEndpointDescriptor',
    'USBOTGDescriptor',
    'USBOTG20Descriptor',
    'USBDebugDescriptor',
    'USBInterfaceAssocDescriptor',
    'OSExtCompatDesc',
)

_MAX_PACKET_SIZE_DICT = {
    ch9.USB_ENDPOINT_XFER_ISOC: (
        1023,   # 0..1023
        1024,   # 0..1024
        1024,   # 0..1024
    ),
    ch9.USB_ENDPOINT_XFER_BULK: (
        64,     # 8, 16, 32, 64
        512,    # 512 only
        1024,   # 1024 only
    ),
    ch9.USB_ENDPOINT_XFER_INT: (
        64,     # 0..64
        1024,   # 0..1024
        1024,   # 1..1024
    ),
}

_MARKER = object()
_EMPTY_DICT = {} # For internal ** falback usage
def getInterfaceInAllSpeeds(interface, endpoint_list, class_descriptor_list=()):
    """
    Produce similar fs, hs and ss interface and endpoints descriptors.
    Should be useful for devices desiring to work in all 3 speeds with maximum
    endpoint wMaxPacketSize. Reduces data duplication from descriptor
    declarations.
    Not intended to cover fancy combinations.

    interface (dict):
      Keyword arguments for
        getDescriptor(USBInterfaceDescriptor, ...)
      in all speeds.
      bNumEndpoints must not be provided.
    endpoint_list (list of dicts)
      Each dict represents an endpoint, and may contain the following items:
      - "endpoint": required, contains keyword arguments for
          getDescriptor(USBEndpointDescriptorNoAudio, ...)
        or
          getDescriptor(USBEndpointDescriptor, ...)
        The with-audio variant is picked when its extra fields are assigned a
        value.
        wMaxPacketSize may be missing, in which case it will be set to the
        maximum size for given speed and endpoint type.
        bmAttributes must be provided.
        If bEndpointAddress is zero (excluding direction bit) on the first
        endpoint, endpoints will be assigned their rank in this list,
        starting at 1. Their direction bit is preserved.
        If bInterval is present on a INT or ISO endpoint, it must be in
        millisecond units (but may not be an integer), and will be converted
        to the nearest integer millisecond for full-speed descriptor, and
        nearest possible interval for high- and super-speed descriptors.
        If bInterval is present on a BULK endpoint, it is set to zero on
        full-speed descriptor and used as provided on high- and super-speed
        descriptors.
      - "superspeed": optional, contains keyword arguments for
          getDescriptor(USBSSEPCompDescriptor, ...)
      - "superspeed_iso": optional, contains keyword arguments for
          getDescriptor(USBSSPIsocEndpointDescriptor, ...)
        Must be provided and non-empty only when endpoint is isochronous and
        "superspeed" dict has "bmAttributes" bit 7 set.
    class_descriptor (list of descriptors of any type)
      Descriptors to insert in all speeds between the interface descriptor and
      endpoint descriptors.

    Returns a 3-tuple of lists:
    - fs descriptors
    - hs descriptors
    - ss descriptors
    """
    interface = getDescriptor(
        USBInterfaceDescriptor,
        bNumEndpoints=len(endpoint_list),
        **interface
    )
    class_descriptor_list = list(class_descriptor_list)
    fs_list = [interface] + class_descriptor_list
    hs_list = [interface] + class_descriptor_list
    ss_list = [interface] + class_descriptor_list
    need_address = (
        endpoint_list[0]['endpoint'].get(
            'bEndpointAddress',
            0,
        ) & ~ch9.USB_DIR_IN == 0
    )
    for index, endpoint in enumerate(endpoint_list, 1):
        endpoint_kw = endpoint['endpoint'].copy()
        transfer_type = endpoint_kw[
            'bmAttributes'
        ] & ch9.USB_ENDPOINT_XFERTYPE_MASK
        fs_max, hs_max, ss_max = _MAX_PACKET_SIZE_DICT[transfer_type]
        if need_address:
            endpoint_kw['bEndpointAddress'] = index | (
                endpoint_kw.get('bEndpointAddress', 0) & ch9.USB_DIR_IN
            )
        klass = (
            USBEndpointDescriptor
            if 'bRefresh' in endpoint_kw or 'bSynchAddress' in endpoint_kw else
            USBEndpointDescriptorNoAudio
        )
        interval = endpoint_kw.pop('bInterval', _MARKER)
        if interval is _MARKER:
            fs_interval = hs_interval = 0
        else:
            if transfer_type == ch9.USB_ENDPOINT_XFER_BULK:
                fs_interval = 0
                hs_interval = interval
            else: # USB_ENDPOINT_XFER_ISOC or USB_ENDPOINT_XFER_INT
                fs_interval = max(1, min(255, round(interval)))
                # 8 is the number of microframes in a millisecond
                hs_interval = max(
                    1,
                    min(16, int(round(1 + math.log(interval * 8, 2)))),
                )
        packet_size = endpoint_kw.pop('wMaxPacketSize', _MARKER)
        if packet_size is _MARKER:
            fs_packet_size = fs_max
            hs_packet_size = hs_max
            ss_packet_size = ss_max
        else:
            fs_packet_size = min(fs_max, packet_size)
            hs_packet_size = min(hs_max, packet_size)
            ss_packet_size = min(ss_max, packet_size)
        fs_list.append(getDescriptor(
            klass,
            wMaxPacketSize=fs_packet_size,
            bInterval=fs_interval,
            **endpoint_kw
        ))
        hs_list.append(getDescriptor(
            klass,
            wMaxPacketSize=hs_packet_size,
            bInterval=hs_interval,
            **endpoint_kw
        ))
        ss_list.append(getDescriptor(
            klass,
            wMaxPacketSize=ss_packet_size,
            bInterval=hs_interval,
            **endpoint_kw
        ))
        ss_companion_kw = endpoint.get('superspeed', _EMPTY_DICT)
        ss_list.append(getDescriptor(
            USBSSEPCompDescriptor,
            **ss_companion_kw
        ))
        ssp_iso_kw = endpoint.get('superspeed_iso', _EMPTY_DICT)
        if bool(ssp_iso_kw) != (
            endpoint_kw.get('bmAttributes', 0) &
            ch9.USB_ENDPOINT_XFERTYPE_MASK ==
            ch9.USB_ENDPOINT_XFER_ISOC and
            bool(ch9.USB_SS_SSP_ISOC_COMP(
                ss_companion_kw.get('bmAttributes', 0),
            ))
        ):
            raise ValueError('Inconsistent isochronous companion')
        if ssp_iso_kw:
            ss_list.append(getDescriptor(
                USBSSPIsocEndpointDescriptor,
                **ssp_iso_kw
            ))
    return (fs_list, hs_list, ss_list)

def getDescriptor(klass, **kw):
    """
    Automatically fills bLength and bDescriptorType.
    """
    # XXX: ctypes Structure.__init__ ignores arguments which do not exist
    # as structure fields. So check it.
    # This is annoying, but not doing it is a huge waste of time for the
    # developer.
    empty = klass()
    assert hasattr(empty, 'bLength')
    assert hasattr(empty, 'bDescriptorType')
    unknown = [x for x in kw if not hasattr(empty, x)]
    if unknown:
        raise TypeError('Unknown fields %r' % (unknown, ))
    # XXX: not very pythonic...
    return klass(
        bLength=ctypes.sizeof(klass),
        # pylint: disable=protected-access
        bDescriptorType=klass._bDescriptorType,
        # pylint: enable=protected-access
        **kw
    )

def getOSDesc(interface, ext_list):
    """
    Return an OS description header.
    interface (int)
        Related interface number.
    ext_list (list of OSExtCompatDesc or OSExtPropDesc)
        List of instances of extended descriptors.
    """
    try:
        ext_type, = {type(x) for x in ext_list}
    except ValueError:
        raise TypeError('Extensions of a single type are required.')
    if issubclass(ext_type, OSExtCompatDesc):
        wIndex = 4
        kw = {
            'b': OSDescHeaderBCount(
                bCount=len(ext_list),
                Reserved=0,
            ),
        }
    elif issubclass(ext_type, OSExtPropDescHead):
        wIndex = 5
        kw = {
            'wCount': len(ext_list),
        }
    else:
        raise TypeError('Extensions of unexpected type')
    ext_list_type = ext_type * len(ext_list)
    klass = type(
        'OSDesc',
        (OSDescHeader, ),
        {
            '_fields_': [
                ('ext_list', ext_list_type),
            ],
        },
    )
    return klass(
        interface=interface,
        dwLength=ctypes.sizeof(klass),
        bcdVersion=1,
        wIndex=wIndex,
        ext_list=ext_list_type(*ext_list),
        **kw
    )

def getOSExtPropDesc(data_type, name, value):
    """
    Returns an OS extension property descriptor.
    data_type (int)
        See wPropertyDataType documentation.
    name (string)
        See PropertyName documentation.
    value (string)
        See PropertyData documentation.
        NULL chars must be explicitely included in the value when needed,
        this function does not add any terminating NULL for example.
    """
    klass = type(
        'OSExtPropDesc',
        (OSExtPropDescHead, ),
        {
            '_fields_': [
                ('bPropertyName', ctypes.c_char * len(name)),
                ('dwPropertyDataLength', le32),
                ('bProperty', ctypes.c_char * len(value)),
            ],
        }
    )
    return klass(
        dwSize=ctypes.sizeof(klass),
        dwPropertyDataType=data_type,
        wPropertyNameLength=len(name),
        bPropertyName=name,
        dwPropertyDataLength=len(value),
        bProperty=value,
    )

#def getDescs(*args, **kw):
#    """
#    Return a legacy format FunctionFS suitable for serialisation.
#    Deprecated as of 3.14 .
#
#    NOT IMPLEMENTED
#    """
#    warnings.warn(
#        DeprecationWarning,
#        'Legacy format, deprecated as of 3.14.',
#    )
#    raise NotImplementedError('TODO')
#    klass = type(
#        'Descs',
#        (DescsHead, ),
#        {
#            'fs_descrs': None, # TODO
#            'hs_descrs': None, # TODO
#        },
#    )
#    return klass(
#        magic=DESCRIPTORS_MAGIC,
#        length=ctypes.sizeof(klass),
#        **kw
#    )

def getDescsV2(flags, fs_list=(), hs_list=(), ss_list=(), os_list=()):
    """
    Return a FunctionFS descriptor suitable for serialisation.

    flags (int)
        Any combination of VIRTUAL_ADDR, EVENTFD, ALL_CTRL_RECIP,
        CONFIG0_SETUP.
    {fs,hs,ss,os}_list (list of descriptors)
        Instances of the following classes:
        {fs,hs,ss}_list:
            USBInterfaceDescriptor
            USBEndpointDescriptorNoAudio
            USBEndpointDescriptor
            USBSSEPCompDescriptor
            USBSSPIsocEndpointDescriptor
            USBOTGDescriptor
            USBOTG20Descriptor
            USBInterfaceAssocDescriptor
            TODO: HID
            All (non-empty) lists must define the same number of interfaces
            and endpoints, and endpoint descriptors must be given in the same
            order, bEndpointAddress-wise.
        os_list:
            OSDesc
    """
    count_field_list = []
    descr_field_list = []
    kw = {}
    for descriptor_list, flag, prefix, allowed_descriptor_klass in (
        (fs_list, HAS_FS_DESC, 'fs', USBDescriptorHeader),
        (hs_list, HAS_HS_DESC, 'hs', USBDescriptorHeader),
        (ss_list, HAS_SS_DESC, 'ss', USBDescriptorHeader),
        (os_list, HAS_MS_OS_DESC, 'os', OSDescHeader),
    ):
        if descriptor_list:
            for index, descriptor in enumerate(descriptor_list):
                if not isinstance(descriptor, allowed_descriptor_klass):
                    raise TypeError(
                        'Descriptor %r of unexpected type: %r' % (
                            index,
                            type(descriptor),
                        ),
                    )
            descriptor_map = [
                ('desc_%i' % x, y)
                for x, y in enumerate(descriptor_list)
            ]
            flags |= flag
            count_name = prefix + 'count'
            descr_name = prefix + 'descr'
            count_field_list.append((count_name, le32))
            descr_type = type(
                't_' + descr_name,
                (ctypes.LittleEndianStructure, ),
                {
                    '_pack_': 1,
                    '_fields_': [
                        (x, type(y))
                        for x, y in descriptor_map
                    ],
                }
            )
            descr_field_list.append((descr_name, descr_type))
            kw[count_name] = len(descriptor_map)
            kw[descr_name] = descr_type(**dict(descriptor_map))
        elif flags & flag:
            raise ValueError(
                'Flag %r set but descriptor list empty, cannot generate type.' % (
                    FLAGS.get(flag),
                )
            )
    klass = type(
        'DescsV2_0x%02x' % (
            flags & (
                HAS_FS_DESC |
                HAS_HS_DESC |
                HAS_SS_DESC |
                HAS_MS_OS_DESC
            ),
            # XXX: include contained descriptors type information ? (and name ?)
        ),
        (DescsHeadV2, ),
        {
            '_fields_': count_field_list + descr_field_list,
        },
    )
    return klass(
        magic=DESCRIPTORS_MAGIC_V2,
        length=ctypes.sizeof(klass),
        flags=flags,
        **kw
    )

def getStrings(lang_dict):
    """
    Return a FunctionFS descriptor suitable for serialisation.

    lang_dict (dict)
        Key: language ID (ex: 0x0409 for en-us)
        Value: list of unicode objects
        All values must have the same number of items.
    """
    field_list = []
    kw = {}
    try:
        str_count = len(next(iter(lang_dict.values())))
    except StopIteration:
        str_count = 0
    else:
        for lang, string_list in lang_dict.items():
            if len(string_list) != str_count:
                raise ValueError('All values must have the same string count.')
            field_id = 'strings_%04x' % lang
            strings = b'\x00'.join(x.encode('utf-8') for x in string_list) + b'\x00'
            field_type = type(
                'String',
                (StringBase, ),
                {
                    '_fields_': [
                        ('strings', ctypes.c_char * len(strings)),
                    ],
                },
            )
            field_list.append((field_id, field_type))
            kw[field_id] = field_type(
                lang=lang,
                strings=strings,
            )
    klass = type(
        'Strings',
        (StringsHead, ),
        {
            '_fields_': field_list,
        },
    )
    return klass(
        magic=STRINGS_MAGIC,
        length=ctypes.sizeof(klass),
        str_count=str_count,
        lang_count=len(lang_dict),
        **kw
    )

def serialise(structure):
    """
    structure (ctypes.Structure)
        The structure to serialise.

    Returns a ctypes.c_char array.
    Does not copy memory.
    """
    return ctypes.cast(
        ctypes.pointer(structure),
        ctypes.POINTER(ctypes.c_char * ctypes.sizeof(structure)),
    ).contents

class EndpointFileBase(io.FileIO):
    """
    File object representing a endpoint. Abstract.
    """
    def _ioctl(self, func, *args, **kw):
        result = fcntl.ioctl(self, func, *args, **kw)
        if result < 0:
            raise IOError(result)
        return result

class Endpoint0File(EndpointFileBase):
    """
    File object exposing ioctls available on endpoint zero.
    """
    def halt(self, request_type):
        """
        Halt current endpoint.
        """
        try:
            if request_type & ch9.USB_DIR_IN:
                self.read(0)
            else:
                self.write(b'')
        except IOError as exc:
            if exc.errno != errno.EL2HLT:
                raise
        else:
            raise ValueError('halt did not return EL2HLT ?')

    def getRealInterfaceNumber(self, interface):
        """
        Returns the host-visible interface number, or None if there is no such
        interface.
        """
        try:
            return self._ioctl(INTERFACE_REVMAP, interface)
        except IOError as exc:
            if exc.errno == errno.EDOM:
                return None
            raise

    # TODO: Add any standard IOCTL in usb_gadget_ops.ioctl ?

class EndpointFile(EndpointFileBase):
    """
    File object exposing ioctls available on non-zero endpoints.
    """
    _halted = False

    def getRealEndpointNumber(self):
        """
        Returns the host-visible endpoint number.
        """
        return self._ioctl(ENDPOINT_REVMAP)

    def clearHalt(self):
        """
        Clears endpoint halt, and resets toggle.

        See drivers/usb/gadget/udc/core.c:usb_ep_clear_halt
        """
        self._ioctl(CLEAR_HALT)
        self._halted = False

    def getFIFOStatus(self):
        """
        Returns the number of bytes in fifo.
        """
        return self._ioctl(FIFO_STATUS)

    def flushFIFO(self):
        """
        Discards Endpoint FIFO content.
        """
        self._ioctl(FIFO_FLUSH)

    def getDescriptor(self):
        """
        Returns the currently active endpoint descriptor
        (depending on current USB speed).
        """
        result = USBEndpointDescriptor()
        self._ioctl(ENDPOINT_DESC, result, True)
        return result

    def _halt(self):
        raise NotImplementedError

    def halt(self):
        """
        Halt current endpoint.
        """
        try:
            self._halt()
        except IOError as exc:
            if exc.errno != errno.EBADMSG:
                raise
        else:
            raise ValueError('halt did not return EBADMSG ?')
        self._halted = True

    def isHalted(self):
        """
        Whether endpoint is currently halted.
        """
        return self._halted

class EndpointINFile(EndpointFile):
    """
    Write-only endpoint file.
    """
    @staticmethod
    def read(*_, **__):
        """
        Always raises IOError.
        """
        raise IOError('File not open for reading')
    readinto = read
    readall = read
    readlines = read
    readline = read

    @staticmethod
    def readable():
        """
        Never readable.
        """
        return False

    def _halt(self):
        super(EndpointINFile, self).read(0)

class EndpointOUTFile(EndpointFile):
    """
    Read-only endpoint file.
    """
    @staticmethod
    def write(*_, **__):
        """
        Always raises IOError.
        """
        raise IOError('File not open for writing')
    writelines = write

    @staticmethod
    def writable():
        """
        Never writable.
        """
        return False

    def _halt(self):
        super(EndpointOUTFile, self).write(b'')

_INFINITY = itertools.repeat(None)
_ONCE = (None, )

class Function(object):
    """
    Pythonic class for interfacing with FunctionFS.

    Properties available:
        function_remote_wakeup_capable (bool)
            Whether the function wishes to be allowed to wake host.
        function_remote_wakeup (bool)
            Whether host has allowed the function to wake it up.
            Set and cleared by onSetup by calling enableRemoteWakeup and
            disableRemoteWakeup, respectively.
    """
    _closed = False
    _ep_list = () # Avoids failing in __del__ when (subclass') __init__ fails.
    function_remote_wakeup_capable = False
    function_remote_wakeup = False

    def __init__(
        self,
        path,
        fs_list=(), hs_list=(), ss_list=(),
        os_list=(),
        lang_dict={},
        all_ctrl_recip=False, config0_setup=False,
    ):
        """
        path (string)
            Path to the functionfs mountpoint (where the ep* files are
            located).
        {fs,hs,ss}_list (list of descriptors)
            XXX: may change to avoid requiring ctype objects.
        os_list (list of descriptors)
            XXX: may change to avoid requiring ctype objects.
        lang_dict (dict)
            Keys: language id (ex: 0x0402 for "us-en").
            Values: List of unicode objects. First item becomes string
                    descriptor 1, and so on. Must contain at least as many
                    string descriptors as the highest string index declared
                    in all descriptors.
        all_ctrl_recip (bool)
            When true, this function will receive all control transactions.
            Useful when implementing non-standard control transactions.
        config0_setup (bool)
            When true, this function will receive control transactions before
            any configuration gets enabled.
        """
        self._path = path
        ep0 = Endpoint0File(os.path.join(path, 'ep0'), 'r+')
        self._ep_list = ep_list = [ep0]
        self._ep_address_dict = ep_address_dict = {}
        flags = 0
        if all_ctrl_recip:
            flags |= ALL_CTRL_RECIP
        if config0_setup:
            flags |= CONFIG0_SETUP
        # Note: serialise does not prevent its argument from being freed and
        # reallocated. Keep strong references to to-serialise values until
        # after they get written.
        desc = getDescsV2(
            flags,
            fs_list=fs_list,
            hs_list=hs_list,
            ss_list=ss_list,
            os_list=os_list,
        )
        ep0.write(serialise(desc))
        # TODO: try v1 on failure ?
        del desc
        # Note: see above.
        strings = getStrings(lang_dict)
        ep0.write(serialise(strings))
        del strings
        for descriptor in ss_list or hs_list or fs_list:
            if descriptor.bDescriptorType == ch9.USB_DT_ENDPOINT:
                assert descriptor.bEndpointAddress not in ep_address_dict, (
                    descriptor,
                    ep_address_dict[descriptor.bEndpointAddress],
                )
                index = len(ep_list)
                ep_address_dict[descriptor.bEndpointAddress] = index
                ep_list.append(
                    (
                        EndpointINFile
                        if descriptor.bEndpointAddress & ch9.USB_DIR_IN
                        else EndpointOUTFile
                    )(
                        os.path.join(path, 'ep%u' % (index, )),
                        'r+',
                    )
                )

    @property
    def ep0(self):
        """
        Endpoint 0, use when handling setup transactions.
        """
        return self._ep_list[0]

    def close(self):
        """
        Close all endpoint file descriptors.
        """
        ep_list = self._ep_list
        while ep_list:
            ep_list.pop().close()
        self._closed = True

    def __del__(self):
        self.close()

    __event_dict = {
        BIND: 'onBind',
        UNBIND: 'onUnbind',
        ENABLE: 'onEnable',
        DISABLE: 'onDisable',
        # SETUP: handled specially
        SUSPEND: 'onSuspend',
        RESUME: 'onResume',
    }

    def __process(self, iterator):
        readinto = self.ep0.readinto
        # FunctionFS can queue up to 4 events, so let's read that much.
        event_len = ctypes.sizeof(Event)
        array_type = Event * 4
        buf = bytearray(ctypes.sizeof(array_type))
        event_list = array_type.from_buffer(buf)
        event_dict = self.__event_dict
        for _ in iterator:
            if self._closed:
                break
            try:
                length = readinto(buf)
            except IOError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise
            if not length:
                # Note: also catches None, returned when ep0 is non-blocking
                break # TODO: test if this happens when ep0 gets closed
                      # (by FunctionFS or in another thread or in a handler)
            count, remainder = divmod(length, event_len)
            assert remainder == 0, (length, event_len)
            for index in range(count):
                event = event_list[index]
                event_type = event.type
                if event_type == SETUP:
                    setup = event.u.setup
                    try:
                        self.onSetup(
                            setup.bRequestType,
                            setup.bRequest,
                            setup.wValue,
                            setup.wIndex,
                            setup.wLength,
                        )
                    except:
                        # On *ANY* exception, halt endpoint
                        self.ep0.halt(setup.bRequestType)
                        raise
                else:
                    getattr(self, event_dict[event.type])()

    def processEventsForever(self):
        """
        Process kernel ep0 events until closed.

        ep0 must be in blocking mode, otherwise behaves like `processEvents`.
        """
        self.__process(_INFINITY)

    def processEvents(self):
        """
        Process at least one kernel event if ep0 is in blocking mode.
        Process any already available event if ep0 is in non-blocking mode.
        """
        self.__process(_ONCE)

    def getEndpoint(self, index):
        """
        Return a file object corresponding to given endpoint index,
        in descriptor list order.
        """
        return self._ep_list[index]

    def getEndpointByAddress(self, address):
        """
        Return a file object corresponding to given endpoint address.
        """
        return self.getEndpoint(self._ep_address_dict[address])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def onBind(self):
        """
        Triggered when FunctionFS signals gadget binding.

        May be overridden in subclass.
        """
        pass

    def onUnbind(self):
        """
        Triggered when FunctionFS signals gadget unbinding.

        May be overridden in subclass.
        """
        pass

    def onEnable(self):
        """
        Called when FunctionFS signals the function was (re)enabled.
        This may happen several times without onDisable being called.
        It must reset the function to its default state.

        May be overridden in subclass.
        """
        self.disableRemoteWakeup()

    def onDisable(self):
        """
        Called when FunctionFS signals the function was (re)disabled.
        This may happen several times without onEnable being called.

        May be overridden in subclass.
        """
        pass

    def disableRemoteWakeup(self):
        """
        Called when host issues a clearFeature request of the "suspend" flag
        on this interface.
        Sets function_remote_wakeup property to False so subsequent getStatus
        requests will return expected value.

        May be overridden in subclass.
        """
        self.function_remote_wakeup = False

    def enableRemoteWakeup(self):
        """
        Called when host issues a setFeature request of the "suspend" flag
        on this interface.
        Sets function_remote_wakeup property to True so subsequent getStatus
        requests will return expected value.

        May be overridden in subclass.
        """
        self.function_remote_wakeup = True

    def onSetup(self, request_type, request, value, index, length):
        """
        Called when a setup USB transaction was received.

        Default implementation:
        - handles USB_REQ_GET_STATUS on interface and endpoints
        - handles USB_REQ_CLEAR_FEATURE(USB_ENDPOINT_HALT) on endpoints
        - handles USB_REQ_SET_FEATURE(USB_ENDPOINT_HALT) on endpoints
        - halts on everything else

        If this method raises anything, endpoint 0 is halted by its caller and
        exception is let through.

        May be overridden in subclass.
        """
        if (request_type & ch9.USB_TYPE_MASK) == ch9.USB_TYPE_STANDARD:
            recipient = request_type & ch9.USB_RECIP_MASK
            is_in = (request_type & ch9.USB_DIR_IN) == ch9.USB_DIR_IN
            if request == ch9.USB_REQ_GET_STATUS:
                if is_in and length == 2:
                    if recipient == ch9.USB_RECIP_INTERFACE:
                        if value == 0:
                            status = 0
                            if index == 0:
                                if self.function_remote_wakeup_capable:
                                    status |= 1 << 0
                                if self.function_remote_wakeup:
                                    status |= 1 << 1
                            self.ep0.write(struct.pack('<H', status)[:length])
                            return
                    elif recipient == ch9.USB_RECIP_ENDPOINT:
                        if value == 0:
                            try:
                                endpoint = self.getEndpoint(index)
                            except IndexError:
                                pass
                            else:
                                status = 0
                                if endpoint.isHalted():
                                    status |= 1 << 0
                                self.ep0.write(
                                    struct.pack('<H', status)[:length],
                                )
                                return
            elif request == ch9.USB_REQ_CLEAR_FEATURE:
                if not is_in and length == 0:
                    if recipient == ch9.USB_RECIP_ENDPOINT:
                        if value == ch9.USB_ENDPOINT_HALT:
                            try:
                                endpoint = self.getEndpoint(index)
                            except IndexError:
                                pass
                            else:
                                endpoint.clearHalt()
                                self.ep0.read(0)
                                return
                    elif recipient == ch9.USB_RECIP_INTERFACE:
                        if value == ch9.USB_INTRF_FUNC_SUSPEND:
                            if self.function_remote_wakeup_capable:
                                self.disableRemoteWakeup()
                                self.ep0.read(0)
                                return
            elif request == ch9.USB_REQ_SET_FEATURE:
                if not is_in and length == 0:
                    if recipient == ch9.USB_RECIP_ENDPOINT:
                        if value == ch9.USB_ENDPOINT_HALT:
                            try:
                                endpoint = self.getEndpoint(index)
                            except IndexError:
                                pass
                            else:
                                endpoint.halt()
                                self.ep0.read(0)
                                return
                    elif recipient == ch9.USB_RECIP_INTERFACE:
                        if value == ch9.USB_INTRF_FUNC_SUSPEND:
                            if self.function_remote_wakeup_capable:
                                self.enableRemoteWakeup()
                                self.ep0.read(0)
                                return
        self.ep0.halt(request_type)

    def onSuspend(self):
        """
        Called when FunctionFS signals the host stops USB traffic.

        May be overridden in subclass.
        """
        pass

    def onResume(self):
        """
        Called when FunctionFS signals the host restarts USB traffic.

        May be overridden in subclass.
        """
        pass
