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
import datetime
import errno
import sys
import time

def main():
    """
    Slowly writes to stdout, without emitting a newline so any output
    buffering (or input for next pipeline command) can be detected.
    """
    now = datetime.datetime.now
    try:
        while True:
            sys.stdout.write(str(now()) + ' ')
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    except IOError as exc:
        if exc.errno != errno.EPIPE:
            raise

if __name__ == '__main__':
    main()
