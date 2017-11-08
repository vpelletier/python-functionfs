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
import ctypes
import errno
import fcntl
import io
import itertools
import os
import struct
import warnings
from .common import (
    USBDescriptorHeader,
    le32,
)
from .ch9 import (
    USBInterfaceDescriptor,
    USBEndpointDescriptorNoAudio,
    USBEndpointDescriptor,
    USB_DIR_IN,
    USB_DT_ENDPOINT,
    USB_ENDPOINT_HALT,
    USB_RECIP_ENDPOINT,
    USB_RECIP_INTERFACE,
    USB_RECIP_MASK,
    USB_REQ_CLEAR_FEATURE,
    USB_REQ_GET_STATUS,
    USB_REQ_SET_FEATURE,
    USB_TYPE_MASK,
    USB_TYPE_STANDARD,
    
)
from .functionfs import (
    DESCRIPTORS_MAGIC, STRINGS_MAGIC, DESCRIPTORS_MAGIC_V2,
    FLAGS,
    HAS_FS_DESC,
    HAS_HS_DESC,
    HAS_SS_DESC,
    HAS_MS_OS_DESC,
    ALL_CTRL_RECIP,
    CONFIG0_SETUP,
    DescsHeadV2,
    DescsHead,
    OSDescHeader,
    OSExtCompatDesc,
    OSExtPropDescHead,
    StringsHead,
    StringBase,
    BIND, UNBIND, ENABLE, DISABLE, SETUP, SUSPEND, RESUME,
    Event,
    FIFO_STATUS, FIFO_FLUSH, CLEAR_HALT, INTERFACE_REVMAP, ENDPOINT_REVMAP, ENDPOINT_DESC,
)

__all__ = (
    'Function',

    # XXX: Not very pythonic...
    'getDescriptor',
    'getOSDesc',
    'getOSExtPropDesc',
    'USBInterfaceDescriptor',
    'USBEndpointDescriptorNoAudio',
    'USBEndpointDescriptor',
    'OSExtCompatDesc',
)

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
        bDescriptorType=klass._bDescriptorType,
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
        ext_type, = {type(ext_list) for x in ext_list}
    except ValueError:
        raise TypeError('Extensions of a single type are required.')
    if isinstance(ext_type, OSExtCompatDesc):
        wIndex = 4
        kw = {
            'b': {
                'bCount': len(ext_list),
                'Reserved': 0,
            },
        }
    elif isinstance(ext_type, OSExtPropDescHead):
        wIndex = 5
        kw = {
            'wCount': len(ext_list),
        }
    else:
        raise TypeError('Extensions of unexpected type')
    klass = type(
        'OSDesc',
        OSDescHeader,
        {
            '_fields_': [
                ('ext_list', ext_type * len(ext_list)),
            ],
        },
    )
    return klass(
        interface=interface,
        dwLength=ctypes.sizeof(klass),
        bcdVersion=1,
        wIndex=wIndex,
        ext_list=ext_list,
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
        OSExtPropDescHead,
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

def getDescs(*args, **kw):
    """
    Return a legacy format FunctionFS suitable for serialisation.
    Deprecated as of 3.14 .

    NOT IMPLEMENTED
    """
    warnings.warn(
        DeprecationWarning,
        'Legacy format, deprecated as of 3.14.',
    )
    raise NotImplementedError('TODO')
    klass = type(
        'Descs',
        (DescsHead, ),
        {
            'fs_descrs': None, # TODO
            'hs_descrs': None, # TODO
        },
    )
    return klass(
        magic=DESCRIPTORS_MAGIC,
        length=ctypes.sizeof(klass),
        **kw
    )

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
            TODO: HID
            TODO: OTG
            TODO: Interface Association
            TODO: SS companion
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
                ('desc_%i' % x,  y)
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
    return (ctypes.c_char * ctypes.sizeof(structure)).from_address(ctypes.addressof(structure))

class EndpointFileBase(io.FileIO):
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
            if request_type & USB_DIR_IN:
                self.read(0)
            else:
                self.write('')
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
                return
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
        return self._halted

class EndpointINFile(EndpointFile):
    """
    Write-only endpoint file.
    """
    @staticmethod
    def read(*args, **kw):
        """
        Always raises IOError.
        """
        raise IOError('File not open for reading')
    readinto = read
    readall = read
    readlines = read
    readline = read

    def readable(self):
        return False

    def _halt(self):
        super(EndpointINFile, self).read(0)

class EndpointOUTFile(EndpointFile):
    """
    Read-only endpoint file.
    """
    @staticmethod
    def write(*args, **kw):
        """
        Always raises IOError.
        """
        raise IOError('File not open for writing')
    writelines = write

    def writable(self):
        return False

    def _halt(self):
        super(EndpointOUTFile, self).write('')

_INFINITY = itertools.repeat(None)
_ONCE = (None, )

class Function(object):
    """
    Pythonic class for interfacing with FunctionFS.
    """
    _closed = False

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
        desc = getDescsV2(
            flags,
            fs_list=fs_list,
            hs_list=hs_list,
            ss_list=ss_list,
            os_list=os_list,
        )
        desc_s = serialise(desc)
        ep0.write(desc_s)
        # TODO: try v1 on failure ?
        strings = getStrings(lang_dict)
        ep0.write(serialise(strings))
        for descriptor in fs_list or hs_list or ss_list:
            if descriptor.bDescriptorType == USB_DT_ENDPOINT:
                assert descriptor.bEndpointAddress not in ep_address_dict, (
                    descriptor,
                    ep_address_dict[descriptor.bEndpointAddress],
                )
                index = len(ep_list)
                ep_address_dict[descriptor.bEndpointAddress] = index
                ep_list.append(
                    (
                        EndpointINFile
                        if descriptor.bEndpointAddress & USB_DIR_IN
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
                    except BaseException:
                        # On *ANY* exception, halt endpoint
                        self.ep0.halt(setup.bRequestType)
                        raise
                else:
                    getattr(self, event_dict[event.type])()

    def processEventsForever(self):
        """
        Process kernel ep0 events until closed.

        ep0 must be in blocking mode, otherwise behaves like `process`.
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
        This may happen several times without onDisable being called, which
        must reset the function to its default state.

        May be overridden in subclass.
        """
        pass

    def onDisable(self):
        """
        Called when FunctionFS signals the function was (re)disabled.
        This may happen several times without onEnable being called.

        May be overridden in subclass.
        """
        pass

    def onSetup(self, request_type, request, value, index, length):
        """
        Called when a setup USB transaction was received.

        Default implementation:
        - handles USB_REQ_GET_STATUS on interface and endpoints
        - handles USB_REQ_CLEAR_FEATURE(USB_ENDPOINT_HALT) on endpoints
        - handles USB_REQ_SET_FEATURE(USB_ENDPOINT_HALT) on endpoints
        - halts on everything else

        If this method raises anything, endpoint 0 is halted by its caller.

        May be overridden in subclass.
        """
        if (request_type & USB_TYPE_MASK) == USB_TYPE_STANDARD:
            recipient = request_type & USB_RECIP_MASK
            is_in = (request_type & USB_DIR_IN) == USB_DIR_IN
            if request == USB_REQ_GET_STATUS:
                if is_in and length == 2:
                    if recipient == USB_RECIP_INTERFACE:
                        self.ep0.write('\x00\x00')
                        return
                    elif recipient == USB_RECIP_ENDPOINT:
                        self.ep0.write(
                            struct.pack(
                                'BB',
                                0,
                                1 if self.getEndpoint(index).isHalted() else 0,
                            ),
                        )
                        return
            elif request == USB_REQ_CLEAR_FEATURE:
                if not is_in and length == 0:
                    if recipient == USB_RECIP_ENDPOINT:
                        if value == USB_ENDPOINT_HALT:
                            self.getEndpoint(index).clearHalt()
                            self.ep0.read(0)
                            return
            elif request == USB_REQ_SET_FEATURE:
                if not is_in and length == 0:
                    if recipient == USB_RECIP_ENDPOINT:
                        if value == USB_ENDPOINT_HALT:
                            self.getEndpoint(index).halt()
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
