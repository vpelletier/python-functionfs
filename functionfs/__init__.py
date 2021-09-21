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
import functools
import io
import itertools
import math
import mmap
import os
import select
import struct
import warnings
import libaio
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
from . import hid
from .hid import (
    getUSBHIDDescriptorClass,
    USB_INTERFACE_PROTOCOL_NONE,
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
    EVENTFD,
    ALL_CTRL_RECIP,
    CONFIG0_SETUP,
    BIND, UNBIND, ENABLE, DISABLE, SETUP, SUSPEND, RESUME,
)
# pylint: enable=no-name-in-module
from ._version import get_versions
__version__ = get_versions()['version']
del get_versions

__all__ = (
    'ch9',
    'hid',
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
    'getUSBHIDDescriptorClass',
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
        raise TypeError('Extensions of a single type are required.') from None
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

def getDescsV2(flags, fs_list=(), hs_list=(), ss_list=(), os_list=(), eventfd=None):
    """
    Return a FunctionFS descriptor suitable for serialisation.

    flags (int)
        Any combination of VIRTUAL_ADDR, ALL_CTRL_RECIP, CONFIG0_SETUP.
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
            USBHIDDescriptor, as provided by getUSBHIDDescriptorClass
            All (non-empty) lists must define the same number of interfaces
            and endpoints, and endpoint descriptors must be given in the same
            order, bEndpointAddress-wise.
        os_list:
            OSDesc
    eventfd (file, None)
        eventfd file instance. If not None, must have fileno method.
    """
    count_field_list = []
    descr_field_list = []
    kw = {}
    if eventfd is not None:
        flags |= EVENTFD
        count_field_list.append(('eventfd', le32))
        kw['eventfd'] = eventfd.fileno()
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
    def __init__(self, path):
        super().__init__(path, 'r+')

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
    def __init__(self, path, submit, eventfd):
        """
        path (string)
            Endpoint file path.
        submit (AIOContext.submit)
            To submit AIOBlocks.
        eventfd (EventFD)
            eventfd to set on all AIOBlocks created for this endpoint.
        """
        super().__init__(path)
        self._submit = submit
        self._eventfd = eventfd

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
        super().read(0)

    def submit(self, buffer_list, user_data=None):
        """
        Non-blocking, zero-copy, AIO-based alternative to "write".

        Queue a list of buffers for sending from this endpoint as a single
        USB transfer (which may be composed of multiple transactions, the last
        one of which will be automatically be made less than wMaxPacketSize so
        host knows we are done sending).

        buffer_list (sized iterable of mutable buffer objects)
            Buffer mutability is needed because they are not internally copied
            and will leave python interpreter. Nothing is expected to mutate
            them, though.
            Also, you should not mutate them between this call and
            corresponding completion event (see onComplete).
        user_data (anything)
            Opaque object passed verbatim to onComplete on completion of this
            transfer.

        May raise OSError(EAGAIN) if there is currently no room for AIO blocks
        (see __init__ in_aio_blocks_max).
        """
        try:
            self._submit((
                libaio.AIOBlock(
                    mode=libaio.AIOBLOCK_MODE_WRITE,
                    target_file=self,
                    buffer_list=buffer_list,
                    offset=0,
                    eventfd=self._eventfd,
                    onCompletion=functools.partial(
                        self._onComplete,
                        buffer_list,
                        user_data,
                    ),
                ),
            ))
        except OSError as exc:
            if exc.errno != errno.EAGAIN:
                raise
            self.onSubmitEAGAIN(buffer_list, user_data)

    def _onComplete(
        self,
        buffer_list, user_data, # from functools.partial
        aio_block, res, res2, # from libaio
    ):
        # res2 is ignored as it just repeats res.
        _ = res2 # silence pylint
        callback_result = self.onComplete(
            buffer_list,
            user_data,
            res,
        )
        if callback_result:
            if res == -errno.ESHUTDOWN:
                raise ValueError(
                    'onComplete cannot ask to resubmit transfer which '
                    'completed with status %i' % res,
                )
            if callback_result is not True:
                aio_block.buffer_list = callback_result
                aio_block.onCompletion = functools.partial(
                    self._onComplete,
                    callback_result,
                    user_data,
                )
            try:
                self._submit((aio_block, ))
            except OSError as exc:
                if exc.errno != errno.EAGAIN:
                    raise
                self.onSubmitEAGAIN(aio_block.buffer_list, user_data)

    # pylint: disable=unused-argument,no-self-use
    def onComplete(self, buffer_list, user_data, status):
        """
        Called when a transfer, queued using submit, completed.

        buffer_list, user_data:
            Values which were provided to submit when initiating this
            transfer.
        status (int)
            Error code if there was an error (negative errno value), number of
            bytes transfered otherwise.

        If a true value is returned, the same transfer is resubmitted:
        - if returned value is True (the builtin), transfer reuses the same
          buffers
        - otherwise, it must be a value similar to buffer_list argument of
          submit method, and will replace buffer in transfer before it gets
          resubmitted. This is especially useful when large buffers are
          prepared, but a different-sized chunk must be submitted on each
          transfer. In which case, this value would be a tuple of memoryview
          instance(s) covering the intended buffer chunk(s), for example.
        Must not return a true value if status is -errno.ESHUTDOWN.

        May be overridden in subclass.
        """
        return False

    def onSubmitEAGAIN(self, buffer_list, user_data):
        """
        Called when submit fails with EAGAIN.
        This may be during a call to submit or when onComplete requested its
        transfer to be resubmitted.

        By default, re-raises the original OSError exception.

        buffer_list, user_data:
            Values which were provided to submit when initiating this
            transfer.

        May be overridden in subclass.
        """
        raise # pylint: disable=misplaced-bare-raise
    # pylint: enable=unused-argument,no-self-use

class EndpointOUTFile(EndpointFile):
    """
    Read-only endpoint file.
    """
    def __init__(self, path, submit, aio_block_list):
        """
        path (string)
            Endpoint file path.
        submit (AIOContext.submit)
            To submit AIOBlocks to after completion.
        aio_block_list (list of AIOBlock)
            Blocks which belong to this endpoint. Modified to bind them to
            this file object (target_file and onCompletion).
        """
        super().__init__(path)
        self._submit = submit
        for aio_block in aio_block_list:
            aio_block.target_file = self
            aio_block.onCompletion = self._onComplete

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
        super().write(b'')

    def _onComplete(self, aio_block, res, res2):
        # res2 is ignored as it just repeats res.
        _ = res2
        if res < 0:
            data = None
            status = res
        else:
            aio_buffer, = aio_block.buffer_list
            data = memoryview(aio_buffer)[:res]
            status = 0
        self.onComplete(
            data=data,
            status=status,
        )
        if res != -errno.ESHUTDOWN:
            # XXX: is it good to resubmit on any other error ?
            # XXX: what should be done on EAGAIN ?
            self._submit((aio_block, ))

    def onComplete(self, data, status):
        """
        Called when this endpoint received data.

        data (memoryview, None)
            Data received, or None if there was an error.
            Once this method returns the underlying buffer will be reused,
            so you must copy any piece you cannot immediately process.
        status (int, None)
            Error code if there was an error (negative errno value), zero
            otherwise.

        May be overridden in subclass.
        """

class Function:
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
    _open = False
    _ep_list = ()
    _in_aio_context = _out_aio_context = None

    function_remote_wakeup_capable = False
    function_remote_wakeup = False

    def __init__(
        self,
        path,
        fs_list=(), hs_list=(), ss_list=(),
        os_list=(),
        lang_dict=(),
        all_ctrl_recip=False, config0_setup=False,
        in_aio_blocks_max=32,
        out_aio_blocks_per_endpoint=2,
        out_aio_blocks_max_packet_count=10,
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
        in_aio_blocks_max (int)
            Maximum number of IN transfers in-flight at any given time.
            Submitting more transfers (using submitIN) will raise
            OSError(EAGAIN).
            There may be a system-wide limit (64k AIO transfers as of 4.18).
        out_aio_blocks_per_endpoint (int)
            Number of OUT transfers to submit for each OUT endpoint.
        out_aio_blocks_max_packet_count (int)
            Maximum number of maximum-size USB packets to receive on each
            OUT endpoint AIO block.
            Memory usage from these buffers will be:
                out_aio_blocks_per_endpoint * sum_OUT_wMaxPacketSize *
                out_aio_blocks_max_packet_count
            So by default 10kB per 512-bytes OUT endpoint will be allocated.
        """
        self._path = path
        self._ep_address_dict = ep_address_dict = {}
        self._eventfd = eventfd = libaio.EventFD(flags=libaio.EFD_NONBLOCK)
        flags = 0
        if all_ctrl_recip:
            flags |= ALL_CTRL_RECIP
        if config0_setup:
            flags |= CONFIG0_SETUP
        self._function_descriptor = getDescsV2(
            flags,
            fs_list=fs_list,
            hs_list=hs_list,
            ss_list=ss_list,
            os_list=os_list,
            eventfd=eventfd,
        )
        self._function_strings = getStrings(dict(lang_dict))
        self._out_aio_block_list = out_aio_block_list = []
        self._out_aio_block_dict = out_aio_block_dict = {}
        self._ep_descriptor_list = ep_descriptor_list = []
        for descriptor in ss_list or hs_list or fs_list:
            if descriptor.bDescriptorType == ch9.USB_DT_ENDPOINT:
                assert descriptor.bEndpointAddress not in ep_address_dict, (
                    descriptor,
                    ep_address_dict[descriptor.bEndpointAddress],
                )
                ep_descriptor_list.append(descriptor)
                is_in = descriptor.bEndpointAddress & ch9.USB_DIR_IN
                index = len(ep_descriptor_list)
                ep_address_dict[descriptor.bEndpointAddress] = index
                if not is_in:
                    out_aio_block_dict[index] = ep_aio_block_list = []
                    for _ in range(out_aio_blocks_per_endpoint):
                        # Using mmap to get a page-aligned buffer. f_fs strongly
                        # recommends aligning IN buffers to wMaxPacketSize
                        # addresses, as this may be required by some UDCs.
                        # Assume wMaxPacketSize will be less than a page.
                        out_block = libaio.AIOBlock(
                            mode=libaio.AIOBLOCK_MODE_READ,
                            buffer_list=(
                                mmap.mmap(
                                    -1, # Anonymous map
                                    out_aio_blocks_max_packet_count *
                                    descriptor.wMaxPacketSize,
                                ),
                            ),
                            offset=0,
                            eventfd=eventfd,
                        )
                        ep_aio_block_list.append(out_block)
                        out_aio_block_list.append(out_block)
        if out_aio_block_list:
            self._out_aio_context = libaio.AIOContext(
                len(out_aio_block_list),
            )
        self._in_aio_context = libaio.AIOContext(
            in_aio_blocks_max,
        )
        # FunctionFS can queue up to 4 events, so let's read that much.
        self._ep0_event_array_type = ep0_event_array_type = Event * 4
        self._ep0_event_size = ctypes.sizeof(Event)
        self._ep0_event_array_size = ctypes.sizeof(ep0_event_array_type)

    def __enter__(self):
        """
        Sends descriptor to kernel and opens endpoint files.
        """
        if self._open:
            raise RuntimeError('Context manager already active')
        try:
            ep0 = Endpoint0File(os.path.join(self._path, 'ep0'))
            self._ep_list = ep_list = [ep0]
            ep0.write(serialise(self._function_descriptor))
            ep0.write(serialise(self._function_strings))
            out_aio_block_dict = self._out_aio_block_dict
            for descriptor in self._ep_descriptor_list:
                index = len(ep_list)
                endpoint_path = os.path.join(self._path, 'ep%u' % (index, ))
                is_in = bool(descriptor.bEndpointAddress & ch9.USB_DIR_IN)
                endpoint_class = self.getEndpointClass(
                    is_in=is_in,
                    descriptor=descriptor,
                )
                if is_in:
                    ep_file = endpoint_class(
                        path=endpoint_path,
                        submit=self._in_aio_context.submit,
                        eventfd=self._eventfd,
                    )
                else:
                    ep_file = endpoint_class(
                        path=endpoint_path,
                        submit=self._out_aio_context.submit,
                        aio_block_list=out_aio_block_dict[index],
                    )
                ep_list.append(ep_file)
            fcntl.fcntl(
                ep0,
                fcntl.F_SETFL,
                fcntl.fcntl(ep0, fcntl.F_GETFL) | os.O_NONBLOCK,
            )
        except:
            self.__unenter()
            raise
        self._open = True
        return self

    def __unenter(self):
        """
        Undo what __enter__ did.
        """
        self._in_aio_context.cancelAll()
        out_aio_context = self._out_aio_context
        if out_aio_context is not None:
            out_aio_context.cancelAll()
            self._out_aio_block_list = []
        ep_list = self._ep_list
        while ep_list:
            ep_list.pop().close()

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Close all endpoint file descriptors.

        Cancels all submitted AIOs and blocks until all are finalised.
        """
        self._open = False
        self.__unenter()

    @property
    def eventfd(self):
        """
        A file-like object which is notified of AIO operation events.
        For polling uses only.
        """
        return self._eventfd

    @property
    def ep0(self):
        """
        Endpoint 0, use when handling setup transactions.

        Non-blocking.
        """
        return self._ep_list[0]

    __event_dict = {
        BIND: 'onBind',
        UNBIND: 'onUnbind',
        ENABLE: 'onEnable',
        DISABLE: 'onDisable',
        # SETUP: handled specially
        SUSPEND: 'onSuspend',
        RESUME: 'onResume',
    }

    # pylint: disable=unused-argument,no-self-use
    def getEndpointClass(self, is_in, descriptor):
        """
        Called during __enter__ when opening endpoint files, to get the class
        to use to represent given it.
        May be overriden to customise specific endpoint classes.

        is_in (bool)
            Endpoint direction: true for IN endpoints, false for OUT
            endpoints.
            Just a more convenient presentation of thuis value, also present
            in the descriptor.
        descriptor (USBEndpointDescriptor{,NoAudio})
            Full descriptor for corresponding endpoint.

        Must return a EndpointINFile-compatible class for IN endpoints,
        and EndpointOUTFile-compatible class for OUT endpoints.
        """
        return EndpointINFile if is_in else EndpointOUTFile
    # pylint: enable=unused-argument,no-self-use

    def processEventsForever(self):
        """
        Process events until either an exception occurs or close is called.
        """
        # Intent: "with select.epoll(1) as epoll:"
        # But it only became a context manager in python3...
        epoll = select.epoll(1)
        try:
            epoll.register(self.eventfd, select.EPOLLIN)
            poll = epoll.poll
            processEvents = self.processEvents
            while self._open:
                try:
                    poll()
                except OSError as exc:
                    if exc.errno != errno.EINTR:
                        raise
                else:
                    processEvents()
        finally:
            epoll.close()

    def processEvents(self):
        """
        Process any available event (both functionfs events and
        AIO completions).

        Non-blocking. Should be called whenever ep0 or eventfd become readable.
        """
        try:
            # Rearm before calling getEvent, so that we do not risk skipping
            # events.
            # Discard returned value because:
            # - we cannot tell which AIO context has how many events ready
            # - even if we could (which is easy: use moar eventfds !) any
            #   discrepancy would either result in a hard-lock (waiting for
            #   events which did not arive yet, and whose AIO blocks may not
            #   have been submitted yet), or result in an early timeout which
            #   would defeat the purpose of observing this count.
            # So if this call succeeds, call non-blocking getEvents on both AIO
            # contexts, and if it fails with EAGAIN skip this (minor
            # optimisation, this means this was likely called to handle ep0
            # events).
            self._eventfd.read()
        except IOError as exc:
            if exc.errno != errno.EAGAIN:
                raise
        else:
            self._in_aio_context.getEvents(0)
            out_aio_context = self._out_aio_context
            if out_aio_context is not None:
                out_aio_context.getEvents(0)
        buf = bytearray(self._ep0_event_array_size)
        length = self.ep0.readinto(buf)
        if length:
            event_dict = self.__event_dict
            count, remainder = divmod(length, self._ep0_event_size)
            assert remainder == 0, (length, self._ep0_event_size)
            event_list = self._ep0_event_array_type.from_buffer(buf)
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

    def onBind(self):
        """
        Triggered when FunctionFS signals gadget binding.

        May be overridden in subclass.
        """

    def onUnbind(self):
        """
        Triggered when FunctionFS signals gadget unbinding.

        May be overridden in subclass.
        """

    def onEnable(self):
        """
        Called when FunctionFS signals the function was (re)enabled.
        This may happen several times without onDisable being called.
        It must reset the function to its default state.
        Also, submits transfers to all IN endpoints (if any).

        May be overridden in subclass.
        """
        self.disableRemoteWakeup()
        if self._out_aio_block_list:
            self._out_aio_context.submit(self._out_aio_block_list)

    def onDisable(self):
        """
        Called when FunctionFS signals the function was (re)disabled.
        This may happen several times without onEnable being called.

        May be overridden in subclass.
        """

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

    def onResume(self):
        """
        Called when FunctionFS signals the host restarts USB traffic.

        May be overridden in subclass.
        """

class HIDFunction(Function):
    """
    Implement HID protocol.
    """
    hid_class_request_dict = {
        # (USB_DIR_IN ?, bRequest) -> method id
        (True, hid.HID_REQ_GET_REPORT): 'getHIDReport',
        (True, hid.HID_REQ_GET_IDLE): 'getHIDIdle',
        (True, hid.HID_REQ_GET_PROTOCOL): 'getHIDProtocol',
        (False, hid.HID_REQ_SET_REPORT): 'setHIDReport',
        (False, hid.HID_REQ_SET_IDLE): 'setHIDIdle',
        (False, hid.HID_REQ_SET_PROTOCOL): 'setHIDProtocol',
    }

    def __init__(
        self,
        path,

        report_descriptor,
        descriptor_dict=(),

        fs_list=(), hs_list=(), ss_list=(),
        os_list=(),
        lang_dict=(),
        all_ctrl_recip=False, config0_setup=False,

        is_boot_device=False,
        protocol=USB_INTERFACE_PROTOCOL_NONE,
        country_code=0,
        in_report_max_length=0,
        out_report_max_length=0,
        full_speed_interval=64,
        high_speed_interval=10,
    ):
        """
        path, ss_list, os_list, lang_dict, all_ctrl_recip, config0_setup
            See Function.__init__ .
        fs_list, hs_list:
            If provided, these values are used instead of automatically
            generating minimal valid descriptors, with IN endpoint at index 1
            and OUT endpoint (if out_report_max_length is non-zero) at
            index 2.

        report_descriptor (bytes)
            The report descriptor. Describes the structure of all reports
            the interface may generate.
        hid_descriptor_list (dict)
            keys (int)
                hid.HID_DT_* values
                Note: hid.HID_DT_REPORT descriptor should rather be provided
                using report_descriptor argument (see above).
            values (list of bytes)
                List of descriptors of this type.
                Note for hid.HID_DT_PHYSICAL: descriptor 0 (see HID 1.11,
                6.2.3) will not be automatically generated.

        All other arguments are for automated descriptor generation, and are
        ignored when fs_list and hs_list are non-empty:
        is_boot_device (bool)
            Whether this interface implements boot device protocol.
        protocol (USB_INTERFACE_PROTOCOL_*)
            Should be provided when is_boot_device is True.
        country_code (int)
            The country code this interface is localised for.
            See table in HID 1.11 specification, 6.2.1 .
        in_report_max_length (int)
            Must be greater than zero to auto-generate a valid descriptor.
            The length of the longest report this interface will produce,
            in bytes.
            If >64 bytes, the devide will be high-speed only.
        out_report_max_length (int)
            If zero, this interface will not have an interrupt OUT endpoint
            (only interrupt IN).
            Otherwise, this is the length of the longest report this interface
            can receive, in bytes.
            If >64 bytes, the devide will be high-speed only.
        full_speed_interval (int)
            Interval for polling endpoint for data transfers.
            In milliseconds units, 1 to 255.
        high_speed_interval (int)
            Interval for polling endpoint for data transfers.
            In 2 ** (n - 1) * 125 microseconds units, 1 to 16:
             1:     125 microseconds
             2:     250
             3:     500
             4:    1000
             5:    2000
             6:    4000
             7:    8000
             8:   16000
             9:   32000
            10:   64000
            11:  128000
            12:  256000
            13:  512000
            14: 1024000
            15: 2048000
            16: 4096000
        """
        descriptor_dict = dict(descriptor_dict)
        descriptor_count = 1 + sum(
            (len(x) for x in descriptor_dict.values()),
        )
        def buildDescriptor(max_packet, interval):
            if (
                in_report_max_length > max_packet or
                out_report_max_length > max_packet
            ):
                return ()
            tail = (hid.USBHIDDescriptorTail * descriptor_count)()
            tail[0].bDescriptorType = hid.HID_DT_REPORT
            tail[0].wDescriptorLength = len(report_descriptor)
            index = 1
            for descriptor_type, descriptor_list in descriptor_dict.items():
                for descriptor in descriptor_list:
                    tail[index].bDescriptorType = descriptor_type
                    tail[index].wDescriptorLength = len(descriptor)
                    index += 1
            result = [
                getDescriptor(
                    USBInterfaceDescriptor,
                    bNumEndpoints=2 if out_report_max_length else 1,
                    bInterfaceClass=ch9.USB_CLASS_HID,
                    bInterfaceSubClass=(
                        hid.USB_INTERFACE_SUBCLASS_BOOT
                        if is_boot_device else
                        hid.USB_INTERFACE_SUBCLASS_NONE
                    ),
                    bInterfaceProtocol=protocol,
                ),
                getDescriptor(
                    hid.getUSBHIDDescriptorClass(descriptor_count),
                    bcdHID=0x0111, # 1.11
                    bCountryCode=country_code,
                    bNumDescriptors=descriptor_count,
                    tail=tail,
                ),
                getDescriptor(
                    USBEndpointDescriptorNoAudio,
                    bEndpointAddress=1 | ch9.USB_DIR_IN,
                    bmAttributes=ch9.USB_ENDPOINT_XFER_INT,
                    wMaxPacketSize=in_report_max_length,
                    bInterval=interval,
                ),
            ]
            if out_report_max_length:
                result.append(
                    getDescriptor(
                        USBEndpointDescriptorNoAudio,
                        bEndpointAddress=2 | ch9.USB_DIR_OUT,
                        bmAttributes=ch9.USB_ENDPOINT_XFER_INT,
                        wMaxPacketSize=out_report_max_length,
                        # XXX: what is the meaning of bInterval on INT OUT ?
                        bInterval=interval,
                    ),
                )
            return result
        super().__init__(
            path,
            fs_list=fs_list or buildDescriptor(
                _MAX_PACKET_SIZE_DICT[ch9.USB_ENDPOINT_XFER_INT][0],
                full_speed_interval,
            ),
            hs_list=hs_list or buildDescriptor(
                _MAX_PACKET_SIZE_DICT[ch9.USB_ENDPOINT_XFER_INT][1],
                high_speed_interval,
            ),
            ss_list=ss_list,
            os_list=os_list,
            lang_dict=lang_dict,
            all_ctrl_recip=all_ctrl_recip,
            config0_setup=config0_setup,
        )
        self.hid_descritptor_dict = hid_descritptor_dict = {
            hid.HID_DT_REPORT: [report_descriptor],
        }
        for descriptor_type, descriptor_list in descriptor_dict.items():
            hid_descritptor_dict.setdefault(
                descriptor_type,
                [],
            ).extend(descriptor_list)

    def onSetup(self, request_type, request, value, index, length):
        if (request_type & ch9.USB_RECIP_MASK) == ch9.USB_RECIP_INTERFACE:
            is_in = (request_type & ch9.USB_DIR_IN) == ch9.USB_DIR_IN
            if (request_type & ch9.USB_TYPE_MASK) == ch9.USB_TYPE_STANDARD:
                if request == ch9.USB_REQ_GET_DESCRIPTOR and is_in:
                    descriptor_list = self.hid_descritptor_dict.get(
                        value >> 8, # Descriptor Type
                        (),
                    )
                    # HID 1.11, 7.1.1:
                    #   A device will return the last descriptor set to
                    #   requests with an index greater than the last number
                    #   defined in the HID descriptor.
                    # Why not just stall to signal to host that it's requesting
                    # garbage ? Oh well.
                    descriptor_index = min(
                        value & 0xff,
                        len(descriptor_list) - 1,
                    )
                    # descriptor_index == -1 if descriptor_list is empty for
                    # give descriptor type
                    if descriptor_index >= 0:
                        self.ep0.write(
                            descriptor_list[descriptor_index][:length],
                        )
                        return
                elif request == ch9.USB_REQ_SET_DESCRIPTOR and not is_in:
                    self.setInterfaceDescriptor(value, index, length)
                    return
            elif (request_type & ch9.USB_TYPE_MASK) == ch9.USB_TYPE_CLASS:
                try:
                    method_id = self.hid_class_request_dict[(is_in, request)]
                except KeyError:
                    pass
                else:
                    getattr(self, method_id)(value, index, length)
                    return
        # Handle basic standard requests, or halt.
        super().onSetup(
            request_type,
            request,
            value,
            index,
            length,
        )

    # pylint: disable=unused-argument
    def setInterfaceDescriptor(self, value, index, length):
        """
        May be overriden and implemented in subclass.

        Return if request was handled,
        Call method on superclass (this class) otherwise so error is signaled
        to host.
        """
        self.ep0.halt(ch9.USB_DIR_IN)

    def getHIDReport(self, value, index, length):
        """
        Must be overridden and implemented in subclass.

        Return if request was handled,
        Call method on superclass (this class) otherwise so error is signaled
        to host.
        """
        self.ep0.halt(ch9.USB_DIR_IN)

    def getHIDIdle(self, value, index, length):
        """
        May be overridden and implemented in subclass.

        Return if request was handled,
        Call method on superclass (this class) otherwise so error is signaled
        to host.
        """
        self.ep0.halt(ch9.USB_DIR_IN)

    def getHIDProtocol(self, value, index, length):
        """
        May be overridden and implemented in subclass.
        Mandatory for boot devices.

        Return if request was handled,
        Call method on superclass (this class) otherwise so error is signaled
        to host.
        """
        self.ep0.halt(ch9.USB_DIR_IN)

    def setHIDReport(self, value, index, length):
        """
        May be overridden and implemented in subclass.

        Return if request was handled,
        Call method on superclass (this class) otherwise so error is signaled
        to host.
        """
        self.ep0.halt(ch9.USB_DIR_OUT)

    def setHIDIdle(self, value, index, length):
        """
        May be overridden and implemented in subclass.

        Return if request was handled,
        Call method on superclass (this class) otherwise so error is signaled
        to host.
        """
        self.ep0.halt(ch9.USB_DIR_OUT)

    def setHIDProtocol(self, value, index, length):
        """
        May be overridden and implemented in subclass.
        Mandatory for boot devices.

        Return if request was handled,
        Call method on superclass (this class) otherwise so error is signaled
        to host.
        """
        self.ep0.halt(ch9.USB_DIR_OUT)
    # pylint: enable=unused-argument
