from __future__ import absolute_import

import ast
import os
import pdb
import sys

from colorama import AnsiToWin32
from colorama import Back
from colorama import Fore
from colorama import Style
from six import string_types

from .util import Fields

DEFAULT_MIN_FILENAME_ALIGNMENT = 40
NO_COLORS = {
    'reset': '',
    'filename': '',
    'colon': '',
    'lineno': '',
    'kind': '',
    'continuation': '',
    'return': '',
    'exception': '',
    'detail': '',
    'vars': '',
    'vars-name': '',
    'call': '',
    'line': '',
    'internal-failure': '',
    'internal-detail': '',
    'source-failure': '',
    'source-detail': '',
}
EVENT_COLORS = {
    'reset': Style.RESET_ALL,
    'normal': Style.NORMAL,
    'filename': '',
    'colon': Style.BRIGHT + Fore.BLACK,
    'lineno': Style.RESET_ALL,
    'kind': Fore.CYAN,
    'continuation': Style.BRIGHT + Fore.BLUE,
    'call': Style.BRIGHT + Fore.BLUE,
    'return': Style.BRIGHT + Fore.GREEN,
    'exception': Style.BRIGHT + Fore.RED,
    'detail': Style.NORMAL,
    'vars': Style.RESET_ALL + Fore.MAGENTA,
    'vars-name': Style.BRIGHT,
    'internal-failure': Style.BRIGHT + Back.RED + Fore.RED,
    'internal-detail': Fore.WHITE,
    'source-failure': Style.BRIGHT + Back.YELLOW + Fore.YELLOW,
    'source-detail': Fore.WHITE,
}
CODE_COLORS = {
    'call': Fore.RESET + Style.BRIGHT,
    'line': Fore.RESET,
    'return': Fore.YELLOW,
    'exception': Fore.RED,
}
MISSING = type('MISSING', (), {'__repr__': lambda _: '?'})()


class Action(object):
    def __call__(self, event):
        raise NotImplementedError()


class Debugger(Fields.klass.kwargs, Action):
    """
    An action that starts ``pdb``.
    """

    def __init__(self, klass=pdb.Pdb, **kwargs):
        self.klass = klass
        self.kwargs = kwargs

    def __call__(self, event):
        """
        Runs a ``pdb.set_trace`` at the matching frame.
        """
        self.klass(**self.kwargs).set_trace(event.frame)


class ColorStreamAction(Fields.stream.force_colors.filename_alignment.repr_limit, Action):
    _stream_cache = {}
    _stream = None
    _tty = None

    def __init__(self,
                 stream=sys.stderr,
                 force_colors=False,
                 filename_alignment=DEFAULT_MIN_FILENAME_ALIGNMENT,
                 repr_limit=1024):
        self.force_colors = force_colors
        self.stream = stream
        self.filename_alignment = max(5, filename_alignment)
        self.repr_limit = repr_limit

    @property
    def stream(self):
        return self._stream

    @stream.setter
    def stream(self, value):
        if isinstance(value, string_types):
            if value in self._stream_cache:
                value = self._stream_cache[value]
            else:
                value = self._stream_cache[value] = open(value, 'a', buffering=0)

        isatty = getattr(value, 'isatty', None)
        if self.force_colors or (isatty and isatty() and os.name != 'java'):
            self._stream = AnsiToWin32(value, strip=False)
            self._tty = True
            self.event_colors = EVENT_COLORS
            self.code_colors = CODE_COLORS
        else:
            self._tty = False
            self._stream = value
            self.event_colors = NO_COLORS
            self.code_colors = NO_COLORS

    def _safe_repr(self, obj):
        limit = self.repr_limit

        try:
            s = repr(obj)
            s = s.replace('\n', r'\n')
            if len(s) > limit:
                cutoff = limit // 2
                return "{} {continuation}[...]{reset} {}".format(s[:cutoff], s[-cutoff:], **self.event_colors)
            else:
                return s
        except Exception as exc:
            return "{internal-failure}!!! FAILED REPR: {internal-detail}{!r}{reset}".format(exc, **self.event_colors)


class CodePrinter(ColorStreamAction):
    """
    An action that just prints the code being executed.

    Args:
        stream (file-like): Stream to write to. Default: ``sys.stderr``.
        filename_alignment (int): Default size for the filename column (files are right-aligned). Default: ``40``.
        force_colors (bool): Force coloring.
    """
    def _safe_source(self, event):
        try:
            lines = event._raw_fullsource.rstrip().splitlines()
            if lines:
                return lines
            else:
                return "{source-failure}??? NO SOURCE: {source-detail}" \
                       "Source code string for module {!r} is empty.".format(event.module, **self.event_colors),
            return lines
        except Exception as exc:
            return "{source-failure}??? NO SOURCE: {source-detail}{!r}".format(exc, **self.event_colors),

    def _format_filename(self, event):
        filename = event.filename or "<???>"
        if len(filename) > self.filename_alignment:
            filename = '[...]{}'.format(filename[5 - self.filename_alignment:])
        return filename

    def __call__(self, event, sep=os.path.sep, join=os.path.join):
        """
        Handle event and print filename, line number and source code. If event.kind is a `return` or `exception` also
        prints values.
        """

        # context = event.tracer
        # alignment = context.filename_alignment = max(
        #     getattr(context, 'filename_alignment', 5),
        #     len(filename)
        # )
        lines = self._safe_source(event)
        self.stream.write("{filename}{:>{align}}{colon}:{lineno}{:<5} {kind}{:9} {code}{}{reset}\n".format(
            self._format_filename(event),
            event.lineno,
            event.kind,
            lines[0],
            align=self.filename_alignment,
            code=self.code_colors[event.kind],
            **self.event_colors
        ))
        for line in lines[1:]:
            self.stream.write("{:>{align}}       {kind}{:9} {code}{}{reset}\n".format(
                "",
                r"   |",
                line,
                align=self.filename_alignment,
                code=self.code_colors[event.kind],
                **self.event_colors
            ))

        if event.kind in ('return', 'exception'):
            self.stream.write("{:>{align}}       {continuation}{:9} {color}{} value: {detail}{}{reset}\n".format(
                "",
                "...",
                event.kind,
                self._safe_repr(event.arg),
                align=self.filename_alignment,
                color=self.event_colors[event.kind],
                **self.event_colors
            ))


class CallPrinter(CodePrinter):
    """
    An action that just prints the code being executed.

    Args:
        stream (file-like): Stream to write to. Default: ``sys.stderr``.
        filename_alignment (int): Default size for the filename column (files are right-aligned). Default: ``40``.
        force_colors (bool): Force coloring.
    """

    def __init__(self, **options):
        super(CallPrinter, self).__init__(**options)
        self.stack = []

    def __call__(self, event, sep=os.path.sep, join=os.path.join):
        """
        Handle event and print filename, line number and source code. If event.kind is a `return` or `exception` also
        prints values.
        """
        filename = self._format_filename(event)
        ident = event.module, event.function

        if event.kind == 'call':
            code = event.code
            self.stack.append(ident)
            self.stream.write("{filename}{:>{align}}{colon}:{lineno}{:<5} {kind}{:9} {}{call}=>{normal} "
                              "{}({}{call}{normal}){reset}\n".format(
                filename,
                event.lineno,
                event.kind,
                '   ' * (len(self.stack) - 1),
                event.function,
                ', '.join('{vars}{vars-name}{0}{vars}={reset}{1}'.format(
                    var,
                    self._safe_repr(event.locals.get(var, MISSING)),
                    **self.event_colors
                ) for var in code.co_varnames[:code.co_argcount]),
                align=self.filename_alignment,
                **self.event_colors
            ))
        elif event.kind in ('return', 'exception'):
            self.stream.write("{filename}{:>{align}}{colon}:{lineno}{:<5} {kind}{:9} {code}{}{}{normal} {}: {reset}{}\n".format(
                filename,
                event.lineno,
                event.kind,
                '   ' * (len(self.stack) - 1),
                {'return': '<=', 'exception': '<!'}[event.kind],
                event.function,
                self._safe_repr(event.arg),
                align=self.filename_alignment,
                code=self.event_colors[event.kind],
                **self.event_colors
            ))
            if self.stack and self.stack[-1] == ident:
                self.stack.pop()
        else:
            self.stream.write("{filename}{:>{align}}{colon}:{lineno}{:<5} {kind}{:9} {reset}{}{}\n".format(
                filename,
                event.lineno,
                event.kind,
                '   ' * len(self.stack),
                event.source.strip(),
                align=self.filename_alignment,
                code=self.code_colors[event.kind],
                **self.event_colors
            ))


class VarsPrinter(Fields.names.globals.stream.filename_alignment, ColorStreamAction):
    """
    An action that prints local variables and optionally global variables visible from the current executing frame.

    Args:
        *names (strings): Names to evaluate. Expressions can be used (will only try to evaluate if all the variables are
            present on the frame.
        stream (file-like): Stream to write to. Default: ``sys.stderr``.
        filename_alignment (int): Default size for the filename column (files are right-aligned). Default: ``40``.
        globals (bool): Allow access to globals. Default: ``False`` (only looks at locals).
        force_colors (bool): Force coloring.
    """

    def __init__(self, *names, **options):
        if not names:
            raise TypeError("VarsPrinter requires at least one variable name/expression.")
        self.names = {
            name: set(self._iter_symbols(name))
            for name in names
        }
        self.globals = options.pop('globals', False)
        super(VarsPrinter, self).__init__(**options)

    @staticmethod
    def _iter_symbols(code):
        """
        Iterate all the variable names in the given expression.

        Example:

        * ``self.foobar`` yields ``self``
        * ``self[foobar]`` yields `self`` and ``foobar``
        """
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Name):
                yield node.id

    def _safe_eval(self, code, event):
        """
        Try to evaluate the given code on the given frame. If failure occurs, returns some ugly string with exception.
        """
        try:
            return eval(code, event.globals if self.globals else {}, event.locals)
        except Exception as exc:
            return "{internal-failure}FAILED EVAL: {internal-detail}{!r}".format(exc, **self.event_colors)

    def __call__(self, event):
        """
        Handle event and print the specified variables.
        """
        first = True
        frame_symbols = set(event.locals)
        if self.globals:
            frame_symbols |= set(event.globals)

        for code, symbols in self.names.items():
            try:
                obj = eval(code, event.globals if self.globals else {}, event.locals)
            except AttributeError:
                continue
            except Exception as exc:
                printout = "{internal-failure}FAILED EVAL: {internal-detail}{!r}".format(exc, **self.event_colors)
            else:
                printout = self._safe_repr(obj)

            if frame_symbols >= symbols:
                self.stream.write("{:>{align}}       {vars}{:9} {vars-name}{} {vars}=> {reset}{}{reset}\n".format(
                    "",
                    "vars" if first else "...",
                    code,
                    printout,
                    align=self.filename_alignment,
                    **self.event_colors
                ))
                first = False
