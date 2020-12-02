# This file is part of python-functionfs
# Copyright (C) 2020  Vincent Pelletier <plr.vincent@gmail.com>
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
Interfaces with /sys/kernel/config/usb_gadget/ to setup an USB gadget capable
of hosting functions.
"""
from __future__ import absolute_import, print_function
import ctypes
import ctypes.util
import errno
import multiprocessing
import os
import signal
import sys
import traceback
import itertools
import tempfile

__all__ = (
    'Gadget',
    'SubprocessFunction',
)

_libc = ctypes.CDLL(
    ctypes.util.find_library('c'),
    use_errno=True,
)
def _checkCCall(result, func, args):
    _ = func # Silence pylint
    _ = args # Silence pylint
    if result < 0:
        raise OSError(ctypes.get_errno())
_mount = _libc.mount
_mount.argtypes = (
    ctypes.c_char_p, # source
    ctypes.c_char_p, # target
    ctypes.c_char_p, # filesystem
    ctypes.c_ulong,  # mountflags
    ctypes.c_char_p, # data
)
_mount.restype = ctypes.c_int
_mount.errcheck = _checkCCall
_umount = _libc.umount
_umount.argtypes = (
    ctypes.c_char_p, # target
)
_umount.restype = ctypes.c_int
_umount.errcheck = _checkCCall

_READY_MARKER = b'ready'

class Gadget(object):
    """
    Declare a gadget, with the strings, configurations, and functions it
    is composed of. Start these functions, and once all are ready, attach
    the gadget definition to a UDC (USB Device Controller).

    Instances of this class are context managers. The work done in __enter__
    and __exit__ (writing to configfs, mounting and unmounting functionfs)
    require elevated privileges (CAP_SYS_ADMIN for {,un}mounting, for example)
    so this code likely needs to run as root.

    You should consider using SubprocessFunction to wrap all your functions,
    with an uid and gid so they can drop privileges and not run as root.
    """
    udb_gadget_path = '/sys/kernel/config/usb_gadget/'
    class_udc_path = '/sys/class/udc/'

    def __init__(
        self,
        config_list,
        idVendor=None,
        idProduct=None,
        lang_dict=(),
        bcdDevice=None,
        bcdUSB=None,
        bDeviceClass=None,
        bDeviceSubclass=None,
        bDeviceProtocol=None,
        name=None,
        udc=None,
    ):
        """
        Declare a gadget.
        Arguments follow the structure of ${configfs}/usb_gadget/ .

        config_list
            Schema:
            [
                {
                    'function_list': [
                        {
                            # function is a callable object, which takes its
                            # functionfs mountpoint path as a named argument,
                            # and returns 3 callables.
                            'function': (mountpoint) -> (
                                # Must block until the function has opened all
                                # its endpoint files.
                                () -> None,
                                # Must tell the function to start winding down.
                                # ex: kill
                                () -> None,
                                # Must block until the function has closed all
                                # its endpoint files.
                                # ex: join
                                () -> None,
                            ),
                            'mount': { # optional
                                'uid': (int), # user owner
                                'gid': (int), # group owner
                                'rmode': (int), # root dir mode
                                'fmode': (int), # files mode
                                'mode': (int), # both of the above
                                # When false and this function process
                                # closes an endpoint file (ex: process exited),
                                # the whole gadget gets forcibly disconnected
                                # from the host. Setting this true lets the
                                # rest of the gadget continue to work, and
                                # let the kernel reject all transfers to this
                                # function.
                                'no_disconnect': (bool),
                            },
                        },
                        ...
                    ],
                    'bmAttributes': int, # optional
                    'MaxPower': int, # optional
                    'lang_dict': { # optional
                        0x0409: {
                            'configuration': u'...',
                        },
                    },
                },
                ...
            ]

        idVendor (int, None)
        idProduct (int, None)
        bcdDevice (int, None)
        bcdUSB (int, None)
        bDeviceProtocol (int, None)
        bDeviceClass (int, None)
        bDeviceSubclass (int, None)
            See the USB specification for device descriptors.
            If None, the kernel default will be used.
            Some of these default values may prevent the gadget from working
            on some hosts: as of this writing, idVendor and idProduct both
            default to zero, and USB devices with these values do not get
            enabled after enumeration on a Linux host.

        lang_dict (dict)
            Keys: language id (ex: 0x0409 for "us-en").
            Values: dicts
                Keys: one of 'serialnumber', 'product', 'manufacturer'
                Value: value for given key, as unicode object
        name (string, None)
            Name of this gadget in configfs. Purely internal to the device.
            If None, a random name will be picked.
        udc (string, None)
            Name of the UDC to use for this gadget.
            If None, there must be exactly one UDC in /sys/class/udc/, which
            will be then used.
        TODO: os desc ?
        """
        if udc is None:
            udc, = os.listdir(self.class_udc_path)
            udc = os.path.basename(udc)
        elif not os.path.exists(os.path.join(self.class_udc_path, udc)):
            raise ValueError('No such UDC')
        self.__udc = udc
        self.__config_list = list(enumerate(
            (
                {
                    'function_list': tuple(enumerate(
                        {
                            'function': function_dict['function'],
                            'mount': b','.join(
                                b'%s=%i' % (
                                    key.encode('ascii'),
                                    cast(function_dict['mount'][key]),
                                )
                                for key, cast in (
                                    ('uid', int),
                                    ('gid', int),
                                    ('rmode', int),
                                    ('fmode', int),
                                    ('mode', int),
                                    ('no_disconnect', bool),
                                )
                                if function_dict['mount'].get(key) is not None
                            )
                        }
                        for function_dict in config_dict['function_list']
                    )),
                    'attribute_dict': {
                        attribute_name: cast(
                            config_dict[attribute_name],
                        ).encode('ascii')
                        for attribute_name, cast in (
                            ('bmAttributes', hex),
                            ('MaxPower', lambda x: '%i' % (x, )),
                        )
                        if config_dict.get(attribute_name) is not None
                    },
                    'lang_dict': {
                        hex(lang): {
                            message_name: message_dict[message_name].encode('utf-8')
                            for message_name in (
                                'configuration',
                            )
                            if message_dict.get(message_name) is not None
                        }
                        for lang, message_dict in config_dict.get('lang_dict', {}).iteritems()
                    },
                }
                for config_dict in config_list
            ),
            1,
        ))
        self.__lang_dict = {
            hex(lang): {
                message_name: message_dict[message_name].encode('utf-8')
                for message_name in (
                    'serialnumber',
                    'product',
                    'manufacturer',
                )
                if message_dict.get(message_name) is not None
            }
            for lang, message_dict in lang_dict.iteritems()
        }
        self.__attribute_dict = {
            name: hex(value).encode('ascii')
            for name, value in {
                'idVendor': idVendor,
                'idProduct': idProduct,
                'bcdDevice': bcdDevice,
                'bcdUSB': bcdUSB,
                'bDeviceProtocol': bDeviceProtocol,
                'bDeviceClass': bDeviceClass,
                'bDeviceSubclass': bDeviceSubclass,
            }.iteritems()
            if value is not None
        }
        self.__name = name
        self.__real_name = None # chosen on __enter__
        self.__mountpoint_dict = {}
        self.__dir_list = []
        self.__link_list = []
        self.__udc_path = None

    def isUDCRegistered(self):
        """
        Call to check whether the UDC is registered.
        If a function with no_disconnect set to false closes its endpoint
        files, the kernel will unregister the UDC from this function.
        This can be used to decide to wind the Gadget down.
        """
        with open(self.__udc_path, 'rb') as udc:
            return bool(udc.read())

    def __writeAttributeDict(self, base, attribute_dict):
        for attribute_name, attribute_value in attribute_dict.iteritems():
            with open(os.path.join(base, attribute_name), 'wb') as attribute_file:
                attribute_file.write(attribute_value)

    def __writeLangDict(self, base, lang_dict):
        result = []
        for lang, message_dict in lang_dict.iteritems():
            lang_path = os.path.join(base, 'strings', lang)
            result.append(lang_path)
            os.mkdir(lang_path)
            self.__writeAttributeDict(lang_path, message_dict)
        return result

    def __enter__(self):
        """
        Write prepared gadget layout to configfs, mount corresponding
        functionfs, start endpoint functions, and attach the gadget to a UDC.
        """
        try:
            self.__enter()
        except Exception:
            self.__unenter()
            raise
        return self

    def __enter(self):
        dir_list = self.__dir_list
        link_list = self.__link_list
        def symlink(source, destination): # pylint: disable=missing-docstring
            os.symlink(source, destination)
            link_list.append(destination)
        def mkdir(path): # pylint: disable=missing-docstring
            os.mkdir(path)
            dir_list.append(path)
        name = self.__name
        if name is None:
            name = tempfile.mkdtemp(
                prefix='g_',
                dir=self.udb_gadget_path,
            )
            dir_list.append(name)
        else:
            name = os.path.join(self.udb_gadget_path, name)
            mkdir(name)
        self.__real_name = name
        dir_list.extend(self.__writeLangDict(name, self.__lang_dict))
        self.__writeAttributeDict(name, self.__attribute_dict)
        function_list = []
        function_number_iterator = itertools.count()
        functions_root = os.path.join(name, 'functions')
        configs_root = os.path.join(name, 'configs')
        for configuration_index, configuration_dict in self.__config_list:
            config_path = os.path.join(configs_root, 'c.%i' % (configuration_index, ))
            mkdir(config_path)
            dir_list.extend(
                self.__writeLangDict(
                    config_path,
                    configuration_dict['lang_dict'],
                ),
            )
            self.__writeAttributeDict(
                config_path,
                configuration_dict['attribute_dict'],
            )
            for function_index, function in configuration_dict['function_list']:
                function_name = 'usb%i' % next(function_number_iterator)
                function_path = os.path.join(
                    functions_root,
                    'ffs.' + function_name,
                )
                function_list.append((
                    function_name,
                    function,
                ))
                mkdir(function_path)
                symlink(
                    function_path,
                    os.path.join(
                        config_path,
                        'function.%i' % (function_index, ),
                    ),
                )
        mountpoint_dict = self.__mountpoint_dict
        wait_list = []
        for function_name, function in function_list:
            mountpoint = tempfile.mkdtemp(
                prefix='ffs.' + function_name + '_',
            )
            b_mountpoint = mountpoint.encode('ascii')
            dir_list.append(mountpoint)
            mountpoint_attr_dict = mountpoint_dict[b_mountpoint] = {}
            _mount(
                function_name.encode('ascii'),
                b_mountpoint,
                b'functionfs',
                0,
                function['mount'],
            )
            wait_function_ready, kill_function, join_function = function['function'](
                mountpoint=mountpoint,
            )
            mountpoint_attr_dict['kill'] = kill_function
            mountpoint_attr_dict['join'] = join_function
            wait_list.append(wait_function_ready)
        while wait_list:
            wait_list.pop()()
        self.__udc_path = udc_path = os.path.join(name, 'UDC')
        with open(udc_path, 'w') as udc:
            udc.write(self.__udc)

    def __exit__(self, exc_type, exc_value, tb):
        self.__unenter()

    def __unenter(self):
        # configfs cleanup is convoluted and rather surprising if it has to
        # be done by the user (ex: rmdir on non-empty directories whose content
        # refuse to be individualy removed). So catch and report (to stderr)
        # exceptions which may come from code out of this module, and continue
        # the teardown.
        # Should the cleanup actually fail, this will give the user the list
        # of operations to do and the order to follow.
        name = self.__real_name
        if not name:
            return
        udc_path = self.__udc_path
        if udc_path:
            with open(udc_path, 'wb') as udc:
                udc.write(b'')
        mountpoint_dict = self.__mountpoint_dict
        noop = lambda: None
        for mountpoint, mountpoint_attr_dict in mountpoint_dict.iteritems():
            try:
                mountpoint_attr_dict.get('kill', noop)()
            except Exception: # pylint: disable=broad-except
                print(
                    'Exception caught while killing function %r' % (
                        mountpoint,
                    ),
                    file=sys.stderr,
                )
                traceback.print_exc()
        for mountpoint, mountpoint_attr_dict in mountpoint_dict.iteritems():
            try:
                mountpoint_attr_dict.get('join', noop)()
            except Exception: # pylint: disable=broad-except
                print(
                    'Exception caught while joining function %r' % (
                        mountpoint,
                    ),
                    file=sys.stderr,
                )
                traceback.print_exc()
            try:
                _umount(mountpoint)
            except OSError as exc:
                # if target is not a mountpoint we can rmdir
                if exc.errno != errno.EINVAL:
                    # on other error, report
                    print(
                        'Failed to unmount %r: %r' % (mountpoint, exc),
                        file=sys.stderr,
                    )
        mountpoint_dict.clear()
        link_list = self.__link_list
        while link_list:
            link = link_list.pop()
            try:
                os.unlink(link)
            except OSError as exc:
                print(
                    'Failed to unlink %r: %r' % (link, exc),
                    file=sys.stderr,
                )
        dir_list = self.__dir_list
        while dir_list:
            directory = dir_list.pop()
            try:
                os.rmdir(directory)
            except OSError as exc:
                print(
                    'Failed to rmdir %r: %r' % (directory, exc),
                    file=sys.stderr,
                )
        self.__real_name = None

class SubprocessFunction(object):
    """
    Instances of this class can be used by Gadget.

    Starts a subprocess changing user and group and calling
    "run" method.
    NOTE: changes working directory to / in the subprocess (like any
    well-behaved service), beware of relative paths !
    """
    def __init__(self, getFunction, uid=None, gid=None):
        """
        getFunction ((path) -> Function)
            Called after forking (and, if applicable, after dropping
            privileges) and before calling the "run" method.
            Created function is available as the "function"
            attribute during "run" method execution.
        uid (int, None)
            User id to drop privileges to.
        gid (int, None)
            Group id to drop privileges to.
        """
        super(SubprocessFunction, self).__init__()
        self.__getFunction = getFunction
        self.__uid = uid
        self.__gid = gid
        self.function = None

    def __call__(self, mountpoint):
        read_pipe, write_pipe = os.pipe()
        process = multiprocessing.Process(
            target=self.__run,
            kwargs={
                'mountpoint': mountpoint,
                'write_pipe': write_pipe,
            },
        )
        process.start()
        os.close(write_pipe)
        def wait(): # pylint: disable=missing-docstring
            with os.fdopen(read_pipe, 'rb', 0) as read_pipef:
                read_pipef.read(len(_READY_MARKER))
        return (
            wait,
            lambda: os.kill(process.pid, signal.SIGINT),
            process.join,
        )

    def __run(self, mountpoint, write_pipe):
        # Note: keeps the pipe open for longer than needed, but ensures that
        # it does get closed.
        with os.fdopen(write_pipe, 'wb', 0) as write_pipef:
            ready_signaled = False
            try:
                os.chdir('/')
                if self.__gid is not None:
                    os.setgid(self.__gid)
                if self.__uid is not None:
                    os.setuid(self.__uid)
                self.function = function = self.__getFunction(path=mountpoint)
                with function:
                    write_pipef.write(_READY_MARKER)
                    ready_signaled = True
                    self.run()
            except Exception:
                # Print traceback before closing write_pipe: parent process may
                # still be waiting for us, in which case it will send us a
                # SIGINT very soon after closing the pipe as part of its own
                # teardown, possibly hiding this error.
                if not ready_signaled:
                    traceback.print_exc()
                # And, in any case, propagate the exception.
                raise
            finally:
                self.function = None

    def run(self):
        """
        Override this method to do something else than just
            self.function.processEventsForever()
        Catches KeyboardInterrupt.
        """
        try:
            self.function.processEventsForever()
        except KeyboardInterrupt:
            pass
