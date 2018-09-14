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
import libaio

# More than one, so we may process one while kernel fills the other.
PENDING_READ_COUNT = 2
MAX_PENDING_WRITE_COUNT = 10
# Large-ish buffer, to tolerate bursts without becoming a context switch storm.
BUF_SIZE = 1024 * 1024

trace = functools.partial(print, file=sys.stderr)

def noIntr(func):
    while True:
        try:
            return func()
        except (IOError, OSError) as exc:
            if exc.errno != errno.EINTR:
                raise

class USBCat(functionfs.Function):
    _enabled = False

    def __init__(self, path, writer, onCanSend, onCannotSend):
        self._aio_context = libaio.AIOContext(
            PENDING_READ_COUNT + MAX_PENDING_WRITE_COUNT,
        )
        self.eventfd = eventfd = libaio.EventFD()
        self._writer = writer
        fs_list, hs_list, ss_list = functionfs.getInterfaceInAllSpeeds(
            interface={
                'bInterfaceClass': functionfs.ch9.USB_CLASS_VENDOR_SPEC,
                'iInterface': 1,
            },
            endpoint_list=[
                {
                    'endpoint': {
                        'bEndpointAddress': 1 | functionfs.ch9.USB_DIR_IN,
                        'bmAttributes': functionfs.ch9.USB_ENDPOINT_XFER_BULK,
                    },
                }, {
                    'endpoint': {
                        'bEndpointAddress': 2 | functionfs.ch9.USB_DIR_OUT,
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
            }
        )
        to_host = self.getEndpoint(2)
        self._aio_recv_block_list = [
            libaio.AIOBlock(
                libaio.AIOBLOCK_MODE_READ,
                to_host,
                [bytearray(BUF_SIZE)],
                0,
                eventfd,
                self._onReceived,
            )
            for _ in xrange(PENDING_READ_COUNT)
        ]
        self._aio_send_block_list = []
        self._real_onCanSend = onCanSend
        self._real_onCannotSend = onCannotSend
        self._need_resume = False

    def close(self):
        self._disable()
        self._aio_context.close()
        super(USBCat, self).close()

    def onBind(self):
        """
        Just for tracing purposes.
        """
        trace('onBind')

    def onUnbind(self):
        """
        Kernel may unbind us without calling disable.
        It does cancel all pending IOs before signaling unbinding, so it would
        be sufficient to mark us as disabled... Except we need to call
        onCannotSend ourselves.
        """
        trace('onUnbind')
        self._disable()

    def onEnable(self):
        """
        The configuration containing this function has been enabled by host.
        Endpoints become working files, so submit some read operations.
        """
        trace('onEnable')
        self._disable()
        self._aio_context.submit(self._aio_recv_block_list)
        self._real_onCanSend()
        self._enabled = True

    def onDisable(self):
        trace('onDisable')
        self._disable()

    def _disable(self):
        """
        The configuration containing this function has been disabled by host.
        Endpoint do not work anymore, so cancel AIO operation blocks.
        """
        if self._enabled:
            self._real_onCannotSend()
            has_cancelled = 0
            for block in self._aio_recv_block_list + self._aio_send_block_list:
                try:
                    self._aio_context.cancel(block)
                except OSError as exc:
                    trace(
                        'cancelling %r raised: %s' % (block, exc),
                    )
                else:
                    has_cancelled += 1
            if has_cancelled:
                noIntr(functools.partial(self._aio_context.getEvents, min_nr=None))
            self._enabled = False

    def onAIOCompletion(self):
        """
        Call when eventfd notified events are available.
        """
        event_count = self.eventfd.read()
        trace('eventfd reports %i events' % event_count)
        # Event though eventfd signaled activity, even though it may give us
        # some number of pending events, some events seem to have been already
        # processed (maybe during io_cancel call ?).
        # So do not trust eventfd value, and do not even trust that there must
        # be even one event to process.
        self._aio_context.getEvents(0)

    def _onReceived(self, block, res, res2):
        if res != -errno.ESHUTDOWN:
            # XXX: is it good to resubmit on any other error ?
            self._aio_context.submit([block])
        if res < 0:
            trace('aio read completion error:', -res)
        else:
            trace('aio read completion received', res, 'bytes')
            self._writer(block.buffer_list[0][:res])

    def _onCanSend(self, block, res, res2):
        if res < 0:
            trace('aio write completion error:', -res)
        else:
            trace('aio write completion sent', res, 'bytes')
        self._aio_send_block_list.remove(block)
        if self._need_resume:
            trace('send queue has room, resume sending')
            self._real_onCanSend()
            self._need_resume = False

    def _onCannotSend(self):
        trace('send queue full, pause sending')
        self._real_onCannotSend()
        self._need_resume = True

    def write(self, value):
        """
        Queue write in kernel.
        value (bytes)
            Value to send.
        """
        aio_block = libaio.AIOBlock(
            libaio.AIOBLOCK_MODE_WRITE,
            self.getEndpoint(1),
            [bytearray(value)],
            0,
            self.eventfd,
            self._onCanSend,
        )
        self._aio_send_block_list.append(aio_block)
        self._aio_context.submit([aio_block])
        if len(self._aio_send_block_list) == MAX_PENDING_WRITE_COUNT:
            self._onCannotSend()

def main(path):
    epoll = select.epoll(3)
    def sender():
        buf = sys.stdin.read(BUF_SIZE)
        trace('sending', len(buf), 'bytes')
        function.write(buf)
    def stopSender():
        try:
            epoll.unregister(sys.stdin)
        except IOError as exc:
            if exc.errno != errno.ENOENT:
                raise
    event_dispatcher_dict = {
        sys.stdin.fileno(): sender,
    }
    def register(file_object, handler):
        epoll.register(file_object, select.EPOLLIN)
        event_dispatcher_dict[file_object.fileno()] = handler
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
        register(function.eventfd, function.onAIOCompletion)
        register(function.ep0, function.processEvents)
        try:
            while True:
                for fd, event in noIntr(epoll.poll):
                    trace('epoll: fd %r got event %r' % (fd, event))
                    event_dispatcher_dict[fd]()
        except (KeyboardInterrupt, EOFError):
            pass

if __name__ == '__main__':
    main(*sys.argv[1:])
