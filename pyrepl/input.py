#   Copyright 2000-2004 Michael Hudson-Doyle <micahel@gmail.com>
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

import unicodedata
from collections import deque

from pyrepl.keymap import compile_keymap, parse_keys


class InputTranslator:
    def push(self, evt):
        pass

    def get(self):
        pass

    def empty(self):
        pass


class KeymapTranslator(InputTranslator):
    """Convert Event objects to Command objects"""

    def __init__(self, keymap, verbose=0, invalid_cls=None, character_cls=None):
        self.verbose = verbose

        self.keymap = keymap
        self.invalid_cls = invalid_cls
        self.character_cls = character_cls
        d = {}
        for keyspec, command in keymap:
            keyseq = tuple(parse_keys(keyspec))
            d[keyseq] = command
        if self.verbose:
            print(d)
        self.k = self.ck = compile_keymap(d, ())
        self.results = deque()
        self.stack = []

    def push(self, evt):
        if self.verbose:
            print("pushed", evt.data, end="")
        key = evt.data
        d = self.k.get(key)
        if isinstance(d, dict):
            if self.verbose:
                print("transition")
            self.stack.append(key)
            self.k = d
        else:
            if d is None:
                if self.verbose:
                    print("invalid")
                if self.stack or len(key) > 1 or unicodedata.category(key) == "C":
                    self.results.append((self.invalid_cls, self.stack + [key]))
                else:
                    # small optimization:
                    self.k[key] = self.character_cls
                    self.results.append((self.character_cls, [key]))
            else:
                if self.verbose:
                    print("matched", d)
                self.results.append((d, self.stack + [key]))
            self.stack = []
            self.k = self.ck

    def get(self):
        if self.results:
            return self.results.popleft()
        else:
            return None

    def empty(self):
        return not self.results
