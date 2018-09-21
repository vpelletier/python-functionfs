#!/usr/bin/env python -u
# This file is part of python-functionfs
# Copyright (C) 2016-2018  Vincent Pelletier <plr.vincent@gmail.com>
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
from __future__ import print_function
import errno
import fcntl
import functools
import os
import select
import sys
import functionfs
import functionfs.ch9

# Large-ish buffer, to tolerate bursts without becoming a context switch storm.
BUF_SIZE = 1024 * 1024

trace = functools.partial(print, file=sys.stderr)

class USBCat(functionfs.Function):
    _need_resume = False

    def __init__(self, path, writer, onCanSend, onCannotSend):
        self._writer = writer
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
        super(USBCat, self).__init__(
            path,
            fs_list=fs_list,
            hs_list=hs_list,
            ss_list=ss_list,
            lang_dict={
                0x0409: [
                    u"USBCat",
                ],
            },
        )
        self._onCanSend = onCanSend
        self._onCannotSend = onCannotSend
        self._stranded_list = []

    def close(self):
        self._onCannotSend()
        super(USBCat, self).close()

    def onBind(self):
        trace('onBind')
        super(USBCat, self).onBind()

    def onUnbind(self):
        trace('onUnbind')
        self._need_resume = False
        self._onCannotSend()
        super(USBCat, self).onUnbind()

    def onEnable(self):
        trace('onEnable')
        super(USBCat, self).onEnable()
        self._onCanSend()

    def onDisable(self):
        trace('onDisable')
        self._need_resume = False
        self._onCannotSend()
        super(USBCat, self).onDisable()

    def onOUTComplete(self, endpoint_index, data, status):
        if data is None:
            trace('aio read completion error:', -status)
        else:
            trace('aio read completion received', len(data), 'bytes')
            self._writer(data.tobytes())

    def onINComplete(self, endpoint_index, buffer_list, user_data, status):
        if status < 0:
            trace('aio write completion error:', -status)
        else:
            trace('aio write completion sent', status, 'bytes')
        if status != -errno.ESHUTDOWN and self._need_resume:
            trace('send queue has room, resume sending')
            self._onCanSend()
            self._need_resume = False

    def submitIN1(self, value):
        """
        Queue write to endpoint 1 in kernel.
        value (bytes)
            Value to send.
        """
        mutable_value = bytearray(value)
        buffer_list = []
        if self._stranded_list:
            buffer_list.extend(self._stranded_list)
            del self._stranded_list[:]
        buffer_list.append(mutable_value)
        try:
            self.submitIN(
                1, # IN endpoint has index 1
                buffer_list,
            )
        except OSError as exc:
            if exc.errno != errno.EAGAIN:
                raise
            self._stranded_list.extend(buffer_list)
            trace('send queue full, pause sending')
            self._onCannotSend()
            self._need_resume = True

def main(path):
    epoll = select.epoll(3)
    def sender():
        # Note: readinto (from io module) would avoid at least one memory copy,
        # but python2 memoryview-of-bytearray incompatibility with
        # ctypes' from_buffer means the buffer would have to have the right
        # size before we know how many bytes we are reading.
        # So just read into an immutable string, which will be cast into a
        # bytearray in submitIN1.
        buf = sys.stdin.read(BUF_SIZE)
        trace('queuing', len(buf), 'bytes')
        function.submitIN1(buf)
    def stopSender():
        try:
            epoll.unregister(sys.stdin)
        except IOError as exc:
            if exc.errno != errno.ENOENT:
                raise
    with USBCat(
        path,
        sys.stdout.write,
        onCanSend=lambda: epoll.register(sys.stdin, select.EPOLLIN),
        onCannotSend=stopSender,
    ) as function:
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

if __name__ == '__main__':
    main(*sys.argv[1:])
