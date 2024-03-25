#   Copyright 2000-2010 Michael Hudson-Doyle <micahel@gmail.com>
#                       Antonio Cuni
#                       Armin Rigo
#
#                        All Rights Reserved
#
#
# Permission to use, copy, modify, and distribute this software and
# its documentation for any purpose is hereby granted without fee,
# provided that the above copyright notice appear in all copies and
# that both that copyright notice and this permission notice appear in
# supporting documentation.
#
# THE AUTHOR MICHAEL HUDSON DISCLAIMS ALL WARRANTIES WITH REGARD TO
# THIS SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS, IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL,
# INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER
# RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF
# CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
# CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

import curses
import errno
import functools
import os
import re
import select
import signal
import struct
import sys
import termios
import time
from fcntl import ioctl

from pyrepl.console import Console, Event
from pyrepl.term import (
    Bell,
    ClearEol,
    ClearScreen,
    ColumnAddress,
    CursorAddress,
    CursorDown,
    CursorInvisible,
    CursorLeft,
    CursorNormal,
    CursorRight,
    CursorUp,
    DeleteCharacter,
    InsertCharacter,
    InvalidTerminal,
    KeypadLocal,
    KeypadXmit,
    PadChar,
    ParmDeleteCharacter,
    ParmDownCursor,
    ParmInsertCharacter,
    ParmLeftCursor,
    ParmRightCursor,
    ParmUpCursor,
    ScrollForward,
    ScrollReverse,
    TermCapability,
    tcgetattr,
    tcsetattr,
)
from pyrepl.trace import trace
from pyrepl.unix_eventqueue import EventQueue

_error = (termios.error, curses.error, InvalidTerminal)

# there are arguments for changing this to "refresh"
SIGWINCH_EVENT = "repaint"

FIONREAD = getattr(termios, "FIONREAD", None)
TIOCGWINSZ = getattr(termios, "TIOCGWINSZ", None)


def maybe_add_baudrate(dict, rate):
    name = "B%d" % rate
    if (key := getattr(termios, name, None)) is not None:
        dict[key] = rate


ratedict = {}
for r in [
    0,
    110,
    115200,
    1200,
    134,
    150,
    1800,
    19200,
    200,
    230400,
    2400,
    300,
    38400,
    460800,
    4800,
    50,
    57600,
    600,
    75,
    9600,
]:
    maybe_add_baudrate(ratedict, r)

del r, maybe_add_baudrate

delayprog = re.compile(b"\\$<([0-9]+)((?:/|\\*){0,2})>")

try:
    poll = select.poll
except AttributeError:
    # this is exactly the minumum necessary to support what we
    # do with poll objects
    class poll:
        def __init__(self):
            pass

        def register(self, fd, flag):
            self.fd = fd

        def poll(self):  # note: a 'timeout' argument would be *milliseconds*
            r, w, e = select.select([self.fd], [], [])
            return r


POLLIN = getattr(select, "POLLIN", None)


class Buffer:
    def __init__(self, svtermstate, output_fd, encoding):
        self.__svtermstate = svtermstate
        self.__output_fd = output_fd
        self.__pad_char = PadChar(self)
        self.__encoding = encoding
        self.__buf = []

    def push(self, item):
        self.__buf.append(item)

    def clear(self):
        self.__buf.clear()

    def __tputs(self, fmt, prog=delayprog):
        """A Python implementation of the curses tputs function; the
        curses one can't really be wrapped in a sane manner.

        I have the strong suspicion that this is complexity that
        will never do anyone any good."""
        # using .get() means that things will blow up
        # only if the bps is actually needed (which I'm
        # betting is pretty unlkely)
        bps = ratedict.get(self.__svtermstate.ospeed)
        while 1:
            m = prog.search(fmt)
            if not m:
                os.write(self.__output_fd, fmt)
                break
            x, y = m.span()
            os.write(self.__output_fd, fmt[:x])
            fmt = fmt[y:]
            delay = int(m.group(1))
            if b"*" in m.group(2):
                delay *= self.height
            if self.__pad_char.supported:
                nchars = (bps * delay) / 1000
                os.write(self.__output_fd, self.__pad_char.text(nchars))
            else:
                time.sleep(float(delay) / 1000.0)

    def flush(self):
        for item in self.__buf:
            if isinstance(item, TermCapability):
                self.__tputs(item.text())
            else:
                os.write(self.__output_fd, item.encode(self.__encoding, "replace"))
        self.clear()


class UnixConsole(Console):
    def __init__(self, f_in=0, f_out=1, term=None, encoding=None):
        if encoding is None:
            encoding = sys.getdefaultencoding()

        self.encoding = encoding

        if isinstance(f_in, int):
            self.input_fd = f_in
        else:
            self.input_fd = f_in.fileno()

        f_out = f_out if isinstance(f_out, int) else f_out.fileno()

        self.pollob = poll()
        self.pollob.register(self.input_fd, POLLIN)
        curses.setupterm(term, f_out)
        self.term = term

        self.__svtermstate = tcgetattr(self.input_fd)
        self.__buffer = Buffer(self.__svtermstate, f_out, self.encoding)

        self._bell = Bell(self.__buffer)
        self._cursor_invsible = CursorInvisible(self.__buffer)
        self._clear_screen = ClearScreen(self.__buffer)
        self._cursor_normal = CursorNormal(self.__buffer)
        self._parm_left_cursor = ParmLeftCursor(self.__buffer)
        self._cursor_left = CursorLeft(self.__buffer)
        self._parm_down_cursor = ParmDownCursor(self.__buffer)
        self._cursor_down = CursorDown(self.__buffer)
        self._parm_right_cursor = ParmRightCursor(self.__buffer)
        self._cursor_right = CursorRight(self.__buffer)
        self._cursor_address = CursorAddress(self.__buffer)
        self._parm_up_cursor = ParmUpCursor(self.__buffer)
        self._cursor_up = CursorUp(self.__buffer)
        self._parm_delete_character = ParmDeleteCharacter(self.__buffer)
        self._delete_character = DeleteCharacter(self.__buffer)
        self._clear_eol = ClearEol(self.__buffer)
        self._column_address = ColumnAddress(self.__buffer)
        self._parm_insert_character = ParmInsertCharacter(self.__buffer)
        self._insert_character = InsertCharacter(self.__buffer)
        self._scroll_forward = ScrollForward(self.__buffer)
        self._scroll_reverse = ScrollReverse(self.__buffer)
        self._keypad_local = KeypadLocal(self.__buffer)
        self._keypad_xmit = KeypadXmit(self.__buffer)

        ## work out how we're going to sling the cursor around
        if self._parm_left_cursor.supported and self._parm_right_cursor.supported:
            self.__move_x = self.__move_x_parm_cursor
        elif self._cursor_left.supported and self._cursor_right.supported:
            self.__move_x = self.__move_x_cursor
        else:
            raise InvalidTerminal("insufficient terminal (horizontal)")

        if self._parm_up_cursor.supported and self._parm_down_cursor.supported:
            self.__move_y = self.__move_y_parm_cursor
        elif self._cursor_up.supported and self._cursor_down.supported:
            self.__move_y = self.__move_y_cursor
        else:
            raise InvalidTerminal("insufficient terminal (vertical)")

        if self._delete_character.supported:
            self.delete_character = self._delete_character
        elif self._parm_delete_character.supported:
            self.delete_character = functools.partial(self._parm_delete_character, 1)
        else:
            self.delete_character = None

        if self._insert_character.supported:
            self.insert_character = self._insert_character
        elif self._parm_insert_character.supported:
            self.insert_character = functools.partial(self._parm_insert_character, 1)
        else:
            self.insert_character = None

        self.__move = self.__move_short

        self.event_queue = EventQueue.create(self.input_fd, self.encoding)
        self.cursor_visible = 1

    def change_encoding(self, encoding):
        self.encoding = encoding

    def refresh(self, screen, c_xy):
        # this function is still too long (over 90 lines)
        cx, cy = c_xy
        if not self.__gone_tall:
            while len(self.screen) < min(len(screen), self.height):
                self.__hide_cursor()
                self.__move(0, len(self.screen) - 1)
                self.__write("\n")
                self.__posxy = 0, len(self.screen)
                self.screen.append("")
        else:
            while len(self.screen) < len(screen):
                self.screen.append("")

        if len(screen) > self.height:
            self.__gone_tall = 1
            self.__move = self.__move_tall

        px, py = self.__posxy
        old_offset = offset = self.__offset
        height = self.height

        # we make sure the cursor is on the screen, and that we're
        # using all of the screen if we can
        if cy < offset:
            offset = cy
        elif cy >= offset + height:
            offset = cy - height + 1
        elif offset > 0 and len(screen) < offset + height:
            offset = max(len(screen) - height, 0)
            screen.append("")

        oldscr = self.screen[old_offset : old_offset + height]
        newscr = screen[offset : offset + height]

        # use hardware scrolling if we have it.
        if old_offset > offset and self._scroll_reverse.supported:
            self.__hide_cursor()
            self._cursor_address(0, 0)
            self.__posxy = 0, old_offset
            for i in range(old_offset - offset):
                self._scroll_reverse()
                oldscr.pop(-1)
                oldscr.insert(0, "")
        elif old_offset < offset and self._scroll_forward.supported:
            self.__hide_cursor()
            self._cursor_address(self.height - 1, 0)
            self.__posxy = 0, old_offset + self.height - 1
            for i in range(offset - old_offset):
                self._scroll_forward()
                oldscr.pop(0)
                oldscr.append("")

        self.__offset = offset

        for (
            y,
            oldline,
            newline,
        ) in zip(range(offset, offset + height), oldscr, newscr):
            if oldline != newline:
                self.__write_changed_line(y, oldline, newline, px)

        y = len(newscr)
        while y < len(oldscr):
            self.__hide_cursor()
            self.__move(0, y)
            self.__posxy = 0, y
            self._clear_eol()
            y += 1

        self.__show_cursor()

        self.screen = screen
        self.move_cursor(cx, cy)
        self.flushoutput()

    def __write_changed_line(self, y, oldline, newline, px):
        # this is frustrating; there's no reason to test (say)
        # self.delete_character inside the loop -- but alternative ways of
        # structuring this function are equally painful (I'm trying to
        # avoid writing code generators these days...)
        x = 0
        minlen = min(len(oldline), len(newline))
        #
        # reuse the oldline as much as possible, but stop as soon as we
        # encounter an ESCAPE, because it might be the start of an escape
        # sequene
        while x < minlen and oldline[x] == newline[x] and newline[x] != "\x1b":
            x += 1
        if oldline[x:] == newline[x + 1 :] and self.insert_character is not None:
            if (
                y == self.__posxy[1]
                and x > self.__posxy[0]
                and oldline[px:x] == newline[px + 1 : x + 1]
            ):
                x = px
            self.__move(x, y)
            self.insert_character()
            self.__write(newline[x])
            self.__posxy = x + 1, y
        elif x < minlen and oldline[x + 1 :] == newline[x + 1 :]:
            self.__move(x, y)
            self.__write(newline[x])
            self.__posxy = x + 1, y
        elif (
            self.delete_character is not None
            and self.insert_character is not None
            and len(newline) == self.width
            and x < len(newline) - 2
            and newline[x + 1 : -1] == oldline[x:-2]
        ):
            self.__hide_cursor()
            self.__move(self.width - 2, y)
            self.__posxy = self.width - 2, y
            self.delete_character()
            self.__move(x, y)
            self.insert_character()
            self.__write(newline[x])
            self.__posxy = x + 1, y
        else:
            self.__hide_cursor()
            self.__move(x, y)
            if len(oldline) > len(newline):
                self._clear_eol()
            self.__write(newline[x:])
            self.__posxy = len(newline), y

        if "\x1b" in newline:
            # ANSI escape characters are present, so we can't assume
            # anything about the position of the cursor.  Moving the cursor
            # to the left margin should work to get to a known position.
            self.move_cursor(0, y)

    def __write(self, text):
        self.__buffer.push(text)

    def __move_y_cursor(self, y):
        dy = y - self.__posxy[1]
        if dy > 0:
            self._cursor_down(dy)
        elif dy < 0:
            self._cursor_up(-dy)

    def __move_y_parm_cursor(self, y):
        dy = y - self.__posxy[1]
        if dy > 0:
            self._parm_down_cursor(dy)
        elif dy < 0:
            self._parm_up_cursor(-dy)

    def __move_x_column_address(self, x):
        if x != self.__posxy[0]:
            self._column_address(x)

    def __move_x_cursor(self, x):
        dx = x - self.__posxy[0]
        if dx > 0:
            self._cursor_right(dx)
        elif dx < 0:
            self._cursor_left(-dx)

    def __move_x_parm_cursor(self, x):
        dx = x - self.__posxy[0]
        if dx > 0:
            self._parm_right_cursor(dx)
        elif dx < 0:
            self._parm_left_cursor(-dx)

    def __move_short(self, x, y):
        self.__move_x(x)
        self.__move_y(y)

    def __move_tall(self, x, y):
        assert 0 <= y - self.__offset < self.height, y - self.__offset
        self._cursor_address(y - self.__offset, x)

    def move_cursor(self, x, y):
        if y < self.__offset or y >= self.__offset + self.height:
            self.event_queue.insert(Event("scroll", None))
        else:
            self.__move(x, y)
            self.__posxy = x, y
            self.flushoutput()

    def prepare(self):
        # per-readline preparations:
        raw = self.__svtermstate.copy()
        raw.iflag &= ~(termios.BRKINT | termios.INPCK | termios.ISTRIP | termios.IXON)
        raw.oflag &= ~(termios.OPOST)
        raw.cflag &= ~(termios.CSIZE | termios.PARENB)
        raw.cflag |= termios.CS8
        raw.lflag &= ~(
            termios.ICANON | termios.ECHO | termios.IEXTEN | (termios.ISIG * 1)
        )
        raw.cc[termios.VMIN] = 1
        raw.cc[termios.VTIME] = 0
        tcsetattr(self.input_fd, termios.TCSADRAIN, raw)

        self.screen = []
        self.height, self.width = self.getheightwidth()

        self.__buffer.clear()

        self.__posxy = 0, 0
        self.__gone_tall = 0
        self.__move = self.__move_short
        self.__offset = 0

        self._keypad_xmit()

        try:
            self.old_sigwinch = signal.signal(signal.SIGWINCH, self.__sigwinch)
        except ValueError:
            pass

    def restore(self):
        self._keypad_local()
        self.flushoutput()
        tcsetattr(self.input_fd, termios.TCSADRAIN, self.__svtermstate)

        if hasattr(self, "old_sigwinch"):
            signal.signal(signal.SIGWINCH, self.old_sigwinch)
            del self.old_sigwinch

    def __sigwinch(self, signum, frame):
        self.height, self.width = self.getheightwidth()
        self.event_queue.insert(Event("resize", None))

    def push_char(self, char):
        trace("push char {char!r}", char=char)
        self.event_queue.push(char)

    def get_event(self, block=1):
        while self.event_queue.empty():
            while 1:  # All hail Unix!
                try:
                    self.push_char(os.read(self.input_fd, 1))
                except OSError as err:
                    if err.errno == errno.EINTR:
                        if not self.event_queue.empty():
                            return self.event_queue.get()
                        else:
                            continue
                    else:
                        raise
                else:
                    break
            if not block:
                break
        return self.event_queue.get()

    def wait(self):
        self.pollob.poll()

    def set_cursor_vis(self, vis):
        if vis:
            self.__show_cursor()
        else:
            self.__hide_cursor()

    def __hide_cursor(self):
        if self.cursor_visible:
            self._cursor_invsible()
            self.cursor_visible = 0

    def __show_cursor(self):
        if not self.cursor_visible:
            self._cursor_normal()
            self.cursor_visible = 1

    def repaint_prep(self):
        if not self.__gone_tall:
            self.__posxy = 0, self.__posxy[1]
            self.__write("\r")
            ns = len(self.screen) * ["\000" * self.width]
            self.screen = ns
        else:
            self.__posxy = 0, self.__offset
            self.__move(0, self.__offset)
            ns = self.height * ["\000" * self.width]
            self.screen = ns

    if TIOCGWINSZ:

        def getheightwidth(self):
            try:
                return int(os.environ["LINES"]), int(os.environ["COLUMNS"])
            except KeyError:
                height, width = struct.unpack(
                    "hhhh", ioctl(self.input_fd, TIOCGWINSZ, b"\000" * 8)
                )[0:2]
                if not height:
                    return 25, 80
                return height, width

    else:

        def getheightwidth(self):
            try:
                return int(os.environ["LINES"]), int(os.environ["COLUMNS"])
            except KeyError:
                return 25, 80

    def forgetinput(self):
        termios.tcflush(self.input_fd, termios.TCIFLUSH)

    def flushoutput(self):
        self.__buffer.flush()

    def finish(self):
        y = len(self.screen) - 1
        while y >= 0 and not self.screen[y]:
            y -= 1
        self.__move(0, min(y, self.height + self.__offset - 1))
        self.__write("\n\r")
        self.flushoutput()

    def beep(self):
        self._bell()
        self.flushoutput()

    if FIONREAD:

        def getpending(self):
            e = Event("key", "", b"")

            while not self.event_queue.empty():
                e2 = self.event_queue.get()
                e.data += e2.data
                e.raw += e.raw

            amount = struct.unpack("i", ioctl(self.input_fd, FIONREAD, b"\0\0\0\0"))[0]
            raw = os.read(self.input_fd, amount)
            data = str(raw, self.encoding, "replace")
            e.data += data
            e.raw += raw
            return e

    else:

        def getpending(self):
            e = Event("key", "", b"")

            while not self.event_queue.empty():
                e2 = self.event_queue.get()
                e.data += e2.data
                e.raw += e.raw

            amount = 10000
            raw = os.read(self.input_fd, amount)
            data = str(raw, self.encoding, "replace")
            e.data += data
            e.raw += raw
            return e

    def clear(self):
        self._clear_screen()
        self.__gone_tall = 1
        self.__move = self.__move_tall
        self.__posxy = 0, 0
        self.screen = []
