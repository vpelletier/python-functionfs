# This file is part of python-functionfs
# Copyright (C) 2020-2021  Vincent Pelletier <plr.vincent@gmail.com>
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
Interfaces with configfs (typically located in /sys/kernel/config/usb_gadget/
to setup an USB gadget capable of hosting functions, and to cleanly wind it
down on exit.
"""

import argparse
import ctypes
import ctypes.util
import errno
import os
import pwd
import signal
import sys
import traceback
import itertools
import tempfile

__all__ = (
    'Gadget',
    'GadgetSubprocessManager',
    'ConfigFunctionBase',
    'ConfigFunctionFFS',
    'ConfigFunctionFFSSubprocess',
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

class Gadget:
    """
    Declare a gadget, with the strings, configurations, and functions it
    is composed of. Start these functions, and once all are ready, attach
    the gadget definition to a UDC (USB Device Controller).

    Instances of this class are context managers. The work done in __enter__
    and __exit__ (writing to configfs, mounting and unmounting functionfs)
    require elevated privileges (CAP_SYS_ADMIN for {,un}mounting, for example)
    so this code likely needs to run as root.
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
        os_desc=None,
    ):
        """
        Declare a gadget.
        Arguments follow the structure of ${configfs}/usb_gadget/ .

        config_list (list of dicts)
            Describes a gadget configuration. Each dict may have the following
            items:
            function_list (required, list of ConfigFunctionBase instances)
                Described an USB function and allow controling its run cycle.
            bmAttributes (optional, int)
            MaxPower (optional, int)
                See the USB specification for Configuration descriptors.
            lang_dict (optional, dict)
                Keys: language (int)
                Values: messages for the language (dict)
                    Keys: 'configuration'
                    Values: unicode object
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
        os_desc (dict, None)
            If dict, it must contain both of the following keys:
            'b_vendor_code': integer in 0..255 range (inclusive).
            'qw_sign': unicode object, must be possible to encode to utf-8 and
            fit in 7 bytes.
        """
        if udc is None:
            try:
                udc_list = os.listdir(self.class_udc_path)
            except FileNotFoundError:
                udc_list = ()
            try:
                udc, = udc_list
            except ValueError:
                raise ValueError(
                    'More than one UDC available'
                    if udc_list else
                    'No UDC available'
                ) from None
            udc = os.path.basename(udc)
        elif not os.path.exists(os.path.join(self.class_udc_path, udc)):
            raise ValueError('No such UDC')
        self.__udc = udc
        self.__config_list = list(enumerate(
            (
                {
                    'function_list': tuple(enumerate(config_dict['function_list'])),
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
                        for lang, message_dict in config_dict.get('lang_dict', {}).items()
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
            for lang, message_dict in dict(lang_dict).items()
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
            }.items()
            if value is not None
        }
        self.__os_desc = (
            ()
            if os_desc is None else
            (
                ('b_vendor_code', os_desc['b_vendor_code'].to_bytes(1, 'big')),
                ('qw_sign', os_desc['qw_sign'].encode('utf-8')),
                ('use', b'1'),
            )
        )
        self.__name = name
        self.__real_name = None # chosen on __enter__
        self.__function_list = []
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

    def __writeAttributeDict(self, base, attribute_dict): # pylint: disable=no-self-use
        for attribute_name, attribute_value in attribute_dict.items():
            with open(os.path.join(base, attribute_name), 'wb') as attribute_file:
                attribute_file.write(attribute_value)

    def __writeLangDict(self, base, lang_dict):
        result = []
        for lang, message_dict in lang_dict.items():
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
        try:
            if name is None:
                name = tempfile.mkdtemp(
                    prefix='g_',
                    dir=self.udb_gadget_path,
                )
                dir_list.append(name)
            else:
                name = os.path.join(self.udb_gadget_path, name)
                mkdir(name)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                exc.strerror = (
                    self.udb_gadget_path +
                    ' does not exist, is libcomposite module loaded ?'
                )
            raise
        self.__real_name = name
        for key, value in self.__os_desc:
            with open(os.path.join(name, key), 'wb') as desc_file:
                desc_file.write(value)
        dir_list.extend(self.__writeLangDict(name, self.__lang_dict))
        self.__writeAttributeDict(name, self.__attribute_dict)
        function_list = self.__function_list
        function_number_iterator = itertools.count()
        name_set = set()
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
                function_name = function.name
                if function_name is None:
                    while True:
                        function_name = 'usb%i' % next(function_number_iterator)
                        if function_name not in name_set:
                            break
                    function.name = function_name
                name_set.add(function_name)
                function_path = os.path.join(
                    functions_root,
                    function.type_name + '.' + function_name,
                )
                try:
                    mkdir(function_path)
                except OSError as exc:
                    if exc.errno == errno.ENOENT:
                        exc.strerror = (
                            'Cannot create function of type %r, is its module '
                            'available ?' % (function.type_name, )
                        )
                    raise
                function_list.append(function)
                function.start(path=function_path)
                symlink(
                    function_path,
                    os.path.join(
                        config_path,
                        'function.%i' % (function_index, ),
                    ),
                )
        for function in function_list:
            function.wait()
        self.__udc_path = udc_path = os.path.join(name, 'UDC')
        try:
            with open(udc_path, 'w') as udc:
                udc.write(self.__udc)
        except (IOError, OSError) as exc:
            if exc.errno == 524: # ENOTSUPP, which is not ENOTSUP
                exc.strerror = 'UDC cannot allocate this many endpoints'
            raise

    def __exit__(self, exc_type, exc_value, tb):
        self.__unenter()
        return False

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
        function_list = self.__function_list
        for function in function_list:
            try:
                function.kill()
            except Exception: # pylint: disable=broad-except
                print(
                    'Exception caught while killing function %r' % (
                        function,
                    ),
                    file=sys.stderr,
                )
                traceback.print_exc()
        while function_list:
            function = function_list.pop()
            try:
                function.join()
            except Exception: # pylint: disable=broad-except
                print(
                    'Exception caught while joining function %r' % (
                        function,
                    ),
                    file=sys.stderr,
                )
                traceback.print_exc()
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

class _UsernameAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        passwd = pwd.getpwnam(values)
        namespace.uid = passwd.pw_uid
        namespace.gid = passwd.pw_gid

def _raiseKeyboardInterrupt(signal_number, stack_frame):
    """
    Make gadget exit if function subprocess exits.
    """
    _ = signal_number # Silence pylint
    _ = stack_frame # Silence pylint
    raise KeyboardInterrupt

class GadgetSubprocessManager(Gadget):
    """
    A Gadget subclass aimed at reducing boilerplate code when involved
    functions are spawned as subprocesses.

    Installs a SIGCHLD handler which raises KeyboardInterrupt, and make
    __exit__ suppress this exception.
    """
    @staticmethod
    def getArgumentParser(**kw):
        """
        Create an argument parser with --udc, --uid, --gid and --username
        arguments.
        Arguments are passed to argparse.ArgumentParser .
        """
        parser = argparse.ArgumentParser(
            epilog='Requires CAP_SYS_ADMIN in order to mount the required '
            'functionfs filesystem, and libcomposite kernel module to be '
            'loaded (or built-in).',
            **kw
        )
        parser.add_argument(
            '--udc',
            help='Name of the UDC to use (default: autodetect)',
        )
        parser.add_argument(
            '--uid',
            type=int,
            help='User to run function as',
        )
        parser.add_argument(
            '--gid',
            type=int,
            help='Group to run function as',
        )
        parser.add_argument(
            '--username',
            action=_UsernameAction,
            help="Run function under this user's uid and gid",
        )
        return parser

    def __init__(self, args, config_list, **kw):
        """
        args (namespace obtained from parse_args)
            To retrieve uid, gid and udc.
        config_list
            Unlike Gadget.__init__, function_list items must be callables
            returning the function instance, and not function instances
            directly.
            This callable will receive uid and gid named arguments with values
            received from args.
        Everything else is passed to Gadget.__init__ .
        """
        for config in config_list:
            config['function_list'] = [
                x(uid=args.uid, gid=args.gid)
                for x in config['function_list']
            ]
        super().__init__(
            udc=args.udc,
            config_list=config_list,
            **kw
        )

    def __enter__(self):
        super().__enter__()
        signal.signal(signal.SIGCHLD, _raiseKeyboardInterrupt)
        # We are on the same terminal as subprocesses, so we will be getting
        # SIGINT at the same time as them. But we need to wait for them to
        # cleanup and then we will be notified by SIGCHLD.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        return self

    def __exit__(self, exc_type, exc_value, tb):
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        result = super().__exit__(exc_type, exc_value, tb)
        return result or isinstance(exc_value, KeyboardInterrupt)

    def waitForever(self): # pylint: disable=no-self-use
        """
        Wait for a signal (including a child exiting).
        """
        while True:
            signal.pause()

class ConfigFunctionBase:
    """
    Base class for config functions.

    Describes the API expected by Gadget (and subclasses).
    """
    name = None

    def __init__(self, name=None):
        """
        name (str)
            Name of this specific instance of this function on this gadget.
            If None, will be generated and set by Gadget when creating the
            functions.
        """
        self.name = name

    @property
    def type_name(self):
        """
        Name of this type of function, as recognised by the kernel.
        Ex: "ffs", "acm", ...
        """
        raise NotImplementedError

    def start(self, path):
        """
        Begin function initialisation: write to function's attributes in
        configfs, open devices/endpoints, spawn processes/threads, ...
        For best performance, this method should be kept short (even on
        functions which do not spawn anything) so such functions can
        initialise in parallel.

        path (str)
            Path to this function in configfs.
        """
        raise NotImplementedError

    def wait(self):
        """
        Block until the function is fully initialised.

        Useful mostly if "start" method spawned processes/threads which need
        to do further initialisation on their own.
        """
        raise NotImplementedError

    def kill(self):
        """
        Tell the function to start winding down.

        Useful mostly if "start" method spawned processes/threads to signal
        them to wind down.
        For best performance, this method should be kept short (even on
        functions which do not spawn anything) so such functions can wind down
        in parallel.
        """
        raise NotImplementedError

    def join(self):
        """
        Block until function has closed all its endpoint files.

        Should undo everything "start" & "wait" methods did before returning.
        """
        raise NotImplementedError

class ConfigFunctionKernel(ConfigFunctionBase):
    """
    Base class for config functions which are implemented in the kernel.
    """
    def __init__(self, config_dict=(), name=None):
        """
        config_dict (dict)
            key (str): path, relative to the function, of the option to set.
            value (str): value of the option
        """
        self.__config_dict = dict(config_dict)
        super().__init__(name=name)

    def start(self, path):
        """
        Apply the content of config_dict.
        """
        for option_path, option_value in self.__config_dict.items():
            option_abspath = os.path.normpath(os.path.join(path, option_path))
            if os.path.commonprefix((path, option_abspath)) != path:
                raise ValueError('Invalid option path: %r' % (option_path, ))
            with open(option_abspath, 'w') as option_file:
                option_file.write(option_value)

    def wait(self):
        """
        No-op.
        """
        return

    def kill(self):
        """
        No-op.
        """
        return

    def join(self):
        """
        No-op.
        """
        return

class ConfigFunctionFFS(ConfigFunctionBase): # pylint: disable=abstract-method
    """
    Base class for functionfs functions.
    """
    type_name = "ffs"
    _mountpoint = None

    def __init__(
        self,
        name=None,
        getFunction=None,
        uid=None,
        gid=None,
        rmode=None,
        fmode=None,
        mode=None,
        no_disconnect=None,
    ):
        """
        getFunction ((path) -> functionfs.Function)
            If non-None, overrides self.getFunction.
            Short-hand to avoid having to subclass for simple functions.
        uid (int, None)
            User id to drop privileges to.
        gid (int, None)
            Group id to drop privileges to.
        rmode (int)
            FunctionFS mountpoint root directory mode.
        fmode (int)
            FunctionFS endpoint file mode.
        mode (int)
            Sets both fmode and rmode.
        no_disconnect (bool)
            When false and this function closes an endpoint file
            (ex: subprocess exited), the whole gadget gets forcibly
            disconnected from its USB host. Setting this true lets the
            rest of the gadget continue to work, and tells the kernel to reject
            all transfers to this function.
        """
        super().__init__(name=name)
        self._getFunction = getFunction
        self._uid = uid
        self._gid = gid
        self._rmode = rmode
        self._fmode = fmode
        self._mode = mode
        self._no_disconnect = no_disconnect

    def getFunction(self, path):
        """
        Called during start (if applicable, after forking and dropping
        privileges). Created function is available as the "function"
        attribute during "run" method execution.

        If not overridden, constructor's getFunction argument is
        mandatory.
        """
        return self._getFunction(path=path)

    def start(self, path):
        """
        Mount functionfs and set _mountpoint.
        """
        function_basename = os.path.basename(path)
        _, function_name = function_basename.split('.')
        mountpoint = tempfile.mkdtemp(
            prefix=function_basename + '_',
        )
        _mount(
            function_name.encode('ascii'),
            mountpoint.encode('ascii'),
            b'functionfs',
            0,
            b','.join(
                b'%s=%i' % (
                    key.encode('ascii'),
                    cast(value),
                )
                for key, cast, value in (
                    ('uid', int, self._uid),
                    ('gid', int, self._gid),
                    ('rmode', int, self._rmode),
                    ('fmode', int, self._fmode),
                    ('mode', int, self._mode),
                    ('no_disconnect', bool, self._no_disconnect),
                )
                if value is not None
            ),
        )
        self._mountpoint = mountpoint

    def join(self):
        """
        Unmount functionfs and clear _mountpoint.
        """
        mountpoint = self._mountpoint
        try:
            _umount(mountpoint.encode('ascii'))
        except OSError as exc:
            # if target is not a mountpoint we can rmdir
            if exc.errno != errno.EINVAL:
                raise
        os.rmdir(mountpoint)
        self._mountpoint = None

class ConfigFunctionFFSSubprocess(ConfigFunctionFFS):
    """
    Function is isolated in a subprocess, allowing a change of user and group.
    NOTE: changes working directory to / in the subprocess (like any
    well-behaved service), beware of relative paths !
    """
    __pid = None
    function = None

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.__read_pipe, self.__write_pipe = os.pipe()

    def start(self, path):
        """
        Start the subprocess.
        In the subprocess: change user, create the function and store it as
        self.function, enter its context, signal readiness to parent process
        and call self.run().
        In the parent process: return the callables expected by Gadget.
        """
        signal.signal(signal.SIGTERM, _raiseKeyboardInterrupt)
        super().start(path)
        self.__pid = pid = os.fork()
        if pid == 0:
            try:
                status = os.EX_OK
                os.close(self.__read_pipe)
                self.__read_pipe = None
                os.chdir('/')
                if self._gid is not None:
                    os.setgid(self._gid)
                if self._uid is not None:
                    os.setuid(self._uid)
                self.function = function = self.getFunction(
                    path=self._mountpoint,
                )
                with function:
                    os.write(self.__write_pipe, _READY_MARKER)
                    self.run()
            except: # pylint: disable=bare-except
                traceback.print_exc()
                status = os.EX_SOFTWARE
            finally:
                os.close(self.__write_pipe)
                os._exit(status) # pylint: disable=protected-access
            # Unreachable
        os.close(self.__write_pipe)
        self.__write_pipe = None

    def wait(self):
        """
        Block until the function subprocess signalled readiness.
        """
        with os.fdopen(self.__read_pipe, 'rb', 0) as read_pipef:
            read_pipef.read(len(_READY_MARKER))

    def kill(self):
        """
        Send the SIGINT signal to function subprocess.
        """
        try:
            os.kill(self.__pid, signal.SIGINT)
        except OSError as exc:
            # If the child process is not running, then all good.
            if exc.errno != errno.ESRCH:
                raise

    def join(self):
        """
        Wait for function subprocess to exit.
        """
        try:
            os.waitpid(self.__pid, 0)
        except OSError as exc:
            # If the child process does not exist (no status to reap), then
            # all good.
            if exc.errno != errno.ECHILD:
                raise
        super().join()

    def run(self):
        """
        Subprocess main code.
        Override this method to do something else than just
            self.function.processEventsForever()
        Catches KeyboardInterrupt.
        """
        try:
            self.function.processEventsForever()
        except KeyboardInterrupt:
            pass
