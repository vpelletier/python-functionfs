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

from collections import deque
import errno
import fcntl
import functools
import os
import select
import sys
import functionfs
from functionfs.gadget import (
    GadgetSubprocessManager,
    ConfigFunctionFFSSubprocess,
)
import functionfs.ch9

# Large-ish buffer, to tolerate bursts without becoming a context switch storm.
BUF_SIZE = 1024 * 1024

trace = functools.partial(print, file=sys.stderr)

class EndpointOUTFile(functionfs.EndpointOUTFile):
    def __init__(self, writer, *args, **kw):
        self.__writer = writer
        super().__init__(*args, **kw)

    def onComplete(self, data, status):
        if data is None:
            trace('aio read completion error:', -status)
        else:
            trace('aio read completion received', len(data), 'bytes')
            self.__writer(data.tobytes())

class EndpointINFile(functionfs.EndpointINFile):
    def __init__(self, onCanSend, onCannotSend, *args, **kw):
        self.__onCanSend = onCanSend
        self.__onCannotSend = onCannotSend
        self.__stranded_buffer_list_queue = deque()
        super().__init__(*args, **kw)

    def onComplete(self, buffer_list, user_data, status):
        if status < 0:
            trace('aio write completion error:', -status)
        else:
            trace('aio write completion sent', status, 'bytes')
        if status != -errno.ESHUTDOWN and self.__stranded_buffer_list_queue:
            buffer_list = self.__stranded_buffer_list_queue.popleft()
            if not self.__stranded_buffer_list_queue:
                trace('send queue has room, resume sending')
                self.__onCanSend()
            return buffer_list
        return None

    def onSubmitEAGAIN(self, buffer_list, user_data):
        self.__stranded_buffer_list_queue.append(buffer_list)
        trace('send queue full, pause sending')
        self.__onCannotSend()

    def forgetStranded(self):
        self.__stranded_buffer_list_queue.clear()

class USBCat(functionfs.Function):
    in_ep = None

    def __init__(self, path, writer, onCanSend, onCannotSend):
        fs_list, hs_list, ss_list = functionfs.getInterfaceInAllSpeeds(
            interface={
                'bInterfaceClass': functionfs.ch9.USB_CLASS_VENDOR_SPEC,
                'iInterface': 1,
            },
            endpoint_list=[
                {
                    'endpoint': {
                        'bEndpointAddress': functionfs.ch9.USB_DIR_IN,
                        'bmAttributes': functionfs.ch9.USB_ENDPOINT_XFER_BULK,
                    },
                }, {
                    'endpoint': {
                        'bEndpointAddress': functionfs.ch9.USB_DIR_OUT,
                        'bmAttributes': functionfs.ch9.USB_ENDPOINT_XFER_BULK,
                    },
                },
            ],
        )
        super().__init__(
            path,
            fs_list=fs_list,
            hs_list=hs_list,
            ss_list=ss_list,
            lang_dict={
                0x0409: [
                    "USBCat",
                ],
            },
        )
        self.__onCanSend = onCanSend
        self.__onCannotSend = onCannotSend
        self.__writer = writer

    def getEndpointClass(self, is_in, descriptor):
        return (
            functools.partial(
                EndpointINFile,
                onCanSend=self.__onCanSend,
                onCannotSend=self.__onCannotSend,
            )
            if is_in else
            functools.partial(
                EndpointOUTFile,
                writer=self.__writer,
            )
        )

    def __enter__(self):
        result = super().__enter__()
        self.in_ep = self.getEndpoint(1)
        return result

    def __exit__(self, exc_type, exc_value, traceback):
        self.__onCannotSend()
        super().__exit__(exc_type, exc_value, traceback)

    def onBind(self):
        trace('onBind')
        super().onBind()

    def onUnbind(self):
        trace('onUnbind')
        self.in_ep.forgetStranded()
        self.__onCannotSend()
        super().onUnbind()

    def onEnable(self):
        trace('onEnable')
        super().onEnable()
        self.__onCanSend()

    def onDisable(self):
        trace('onDisable')
        self.in_ep.forgetStranded()
        self.__onCannotSend()
        super().onDisable()

    def onSuspend(self):
        trace('onSuspend')
        super().onSuspend()

    def onResume(self):
        trace('onResume')
        super().onResume()

class SubprocessCat(ConfigFunctionFFSSubprocess):
    __epoll = None

    def __init__(self, **kw):
        super().__init__(**kw)
        self.__out_encoding = getattr(sys.stdout, 'encoding', None)

    def getFunction(self, path):
        return USBCat(
            path=path,
            writer=self.__writer,
            onCanSend=self.__onCanSend,
            onCannotSend=self.__stopSender,
        )

    def __writer(self, value):
        sys.stdout.write(
            value
            if self.__out_encoding is None else
            value.decode('utf-8', errors='replace')
        )
        sys.stdout.flush()

    def __onCanSend(self):
        self.__epoll.register(sys.stdin, select.EPOLLIN)

    def __stopSender(self):
        try:
            self.__epoll.unregister(sys.stdin)
        except IOError as exc:
            if exc.errno != errno.ENOENT:
                raise

    def start(self, *args, **kw):
        super().start(*args, **kw)
        # Let the subprocess get all the input.
        sys.stdin.close()

    def run(self):
        """
        This implementation does not call ConfigFunctionFFSSubprocess.run, as
        it implements its own event handling loop involving function's file
        descriptors.
        """
        self.__epoll = epoll = select.epoll(3)
        def sender():
            # Note: readinto (from io module) would avoid at least one memory copy,
            # but python2 memoryview-of-bytearray incompatibility with
            # ctypes' from_buffer means the buffer would have to have the right
            # size before we know how many bytes we are reading.
            # So just read and convert into the mutable buffer required by submit.
            value = sys.stdin.read(BUF_SIZE)
            if not value:
                raise EOFError
            encode = getattr(value, 'encode', None)
            if encode is not None:
                value = encode('utf-8', errors="replace")
            buf = bytearray(value)
            trace('queuing', len(buf), 'bytes')
            in_ep_submit([buf])
        function = self.function
        in_ep_submit = function.in_ep.submit
        fcntl.fcntl(
            sys.stdin,
            fcntl.F_SETFL,
            fcntl.fcntl(sys.stdin, fcntl.F_GETFL) | os.O_NONBLOCK,
        )
        event_dispatcher_dict = {
            sys.stdin.fileno(): sender,
            function.eventfd.fileno(): function.processEvents,
        }
        epoll.register(function.eventfd, select.EPOLLIN)
        poll = epoll.poll
        try:
            while True:
                try:
                    event_list = poll()
                except OSError as exc:
                    if exc.errno != errno.EINTR:
                        raise
                else:
                    for fd, event in event_list:
                        trace('epoll: fd %r got event %r' % (fd, event))
                        event_dispatcher_dict[fd]()
        except (KeyboardInterrupt, EOFError):
            pass

def main():
    with GadgetSubprocessManager(
        args=GadgetSubprocessManager.getArgumentParser(
            description='Example implementation of an USB gadget establishing '
            'a bidirectional pipe with the host.',
        ).parse_args(),
        config_list=[
            {
                'function_list': [
                    SubprocessCat,
                ],
                'MaxPower': 500,
                'lang_dict': {
                    0x409: {
                        'configuration': 'cat demo function',
                    },
                },
            }
        ],
        idVendor=0x1d6b, # Linux Foundation
        idProduct=0x0104, # Multifunction Composite Gadget
        lang_dict={
            0x409: {
                'product': 'cat demo',
                'manufacturer': 'python-functionfs',
            },
        },
    ) as gadget:
        gadget.waitForever()

if __name__ == '__main__':
    main()
