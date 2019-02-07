# -*- coding: utf-8 -*-
#
# Copyright (c) 2018-2019, Geoffrey M. Poore
# All rights reserved.
#
# Licensed under the BSD 3-Clause License:
# http://opensource.org/licenses/BSD-3-Clause
#


import collections
import json
import os
import pathlib
import platform
import re
import subprocess
import tempfile
from typing import Optional, Sequence, Union
import warnings
from .base import Converter, CodeChunk
from .. import err




class PandocError(err.CodebraidError):
    pass




class PandocCodeChunk(CodeChunk):
    def __init__(self,
                 node: dict,
                 parent_node_list: list,
                 parent_node_list_index: int,
                 source_name: str,
                 source_start_line_number: int):
        self.node = node
        self.parent_node_list = parent_node_list
        self.parent_node_list_index = parent_node_list_index

        options = {}
        pandoc_id = ''
        pandoc_classes = []
        pandoc_kvpairs = []
        node_id, node_classes, node_kvpairs = node['c'][0]
        code = node['c'][1]

        # Once design stabilizes, it may be worth converting this in to a set
        # of class and attribute processing functions, to guarantee consistent
        # handling
        if node_id:
            options['label'] = node_id
            pandoc_id = node_id
        inline = node['t'] == 'Code'
        codebraid_command = None
        for n, c in enumerate(node_classes):
            if c.startswith('cb.'):
                if codebraid_command is not None:
                    raise err.SourceError('Only one Codebraid command can be applied per code chunk', source_name, start_line_number)
                codebraid_command = c.split('.', 1)[1]
            elif c in ('lineAnchors', 'line-anchors', 'line_anchors'):
                if 'lineAnchors' in pandoc_classes:
                    raise err.SourceError('Duplicate line anchor class for code chunk', source_name, start_line_number)
                pandoc_classes.append('lineAnchors')
            elif c in ('line_numbers', 'numberLines', 'number-lines', 'number_lines'):
                if 'line_numbers' in options:
                    raise err.SourceError('Duplicate line numbering class for code chunk', source_name, start_line_number)
                options['line_numbers'] = True
                pandoc_classes.append('numberLines')
            else:
                if n > 0:
                    raise err.SourceError('The language/format must be the first class for code chunk', source_name, start_line_number)
                if 'lang' in options:
                    raise err.SourceError('Unknown non-Codebraid class', source_name, start_line_number)
                options['lang'] = c
                pandoc_classes.insert(0, c)
        for k, v in node_kvpairs:
            if k == 'hide':
                if 'hide' in options:
                    raise err.SourceError('Duplicate "hide" attribute for code chunk', source_name, start_line_number)
                options['hide'] = v
            elif k in ('first_number', 'startFrom', 'start-from', 'start_from'):
                if 'first_number' in options:
                    raise err.SourceError('Duplicate first line number attribute for code chunk', source_name, start_line_number)
                options['first_number'] = v
            elif k in ('label', 'name'):
                if 'label' in options:
                    if node_id:
                        raise err.SourceError('Code chunk can have only one label/name; already have #<id>', source_name, start_line_number)
                    raise err.SourceError('Code chunk can have only one label/name', source_name, start_line_number)
                if not self._pandoc_id_re.match(v):
                    raise err.SourceError('Code chunk label/name must be valid Pandoc identifier: <letter>(<alphanum>|"-_:.")*', source_name, start_line_number)
                options['label'] = v
                pandoc_id = v
            elif k == 'lang':
                if 'lang' in options:
                    raise err.SourceError('Duplicate lang for code chunk', source_name, start_line_number)
                options['lang'] = v
                pandoc_classes.insert(0, v)
            elif k in ('lineAnchors', 'line-anchors', 'line_anchors'):
                if 'lineAnchors' in pandoc_classes:
                    raise err.SourceError('Duplicate line anchor attribute for code chunk', source_name, start_line_number)
                pandoc_classes.append('lineAnchors')
            elif k in ('line_numbers', 'numberLines', 'number-lines', 'number_lines'):
                if 'line_numbers' in options:
                    raise err.SourceError('Duplicate line numbering attribute for code chunk', source_name, start_line_number)
                options['line_numbers'] = v
                pandoc_classes.append('numberLines')
            elif k == 'session':
                if 'session' in options:
                    raise err.SourceError('Duplicate "session" attribute for code chunk', source_name, start_line_number)
                options['session'] = v
            elif k == 'show':
                if 'show' in options:
                    raise err.SourceError('Duplicate "show" attribute for code chunk', source_name, start_line_number)
                options['show'] = v
            else:
                raise err.SourceError('Unknown or currently unsupported attribute {0}="{1}"'.format(k, v), source_name, start_line_number)
        self.pandoc_id = pandoc_id
        self.pandoc_classes = pandoc_classes
        self.pandoc_kvpairs = pandoc_kvpairs
        super().__init__(codebraid_command, code, options, source_name, source_start_line_number=source_start_line_number, inline=inline)

    # identifier approximation for Pandoc/Readers/Markdown.hs
    _pandoc_id_re = re.compile(r'(?!\d|_)(?:\w+|[-:.]+)+')


    def output_nodes(self):
        if not self.inline and self.options['line_numbers']:
            first_num = self.options['first_number']
            if first_num == 'next':
                first_num = str(self.code_start_line_number)
            else:
                first_num = str(first_num)
            self.pandoc_kvpairs.append(['startFrom', first_num])
        t_code = 'Code' if self.inline else 'CodeBlock'
        t_raw = 'RawInline' if self.inline else 'RawBlock'
        nodes = []
        for display, format in self.options['show'].items():
            if display == 'code':
                code = self.code
                if not self.inline:
                    code = code[:-1]
                nodes.append({'t': t_code, 'c': [[self.pandoc_id, self.pandoc_classes, self.pandoc_kvpairs], code]})
            elif display == 'expression':
                if format in ('verbatim', 'autoverbatim'):
                    expression = self.expression
                    if (format == 'verbatim' and expression is not None) or format == 'autoverbatim':
                        if expression is None:
                            expression = ''
                        nodes.append({'t': t_code, 'c': [['', [], []], expression]})
                elif format == 'raw':
                    if self.expression is not None:
                        nodes.append({'t': t_raw, 'c': ['markdown', self.expression]})
                else:
                    raise ValueError
            elif display == 'stdout':
                if format in ('verbatim', 'autoverbatim'):
                    stdout = self.stdout
                    if (format == 'verbatim' and stdout is not None) or format == 'autoverbatim':
                        if stdout is None:
                            stdout = ''
                        if not self.inline:
                            stdout = stdout[:-1]
                        nodes.append({'t': t_code, 'c': [['', ['stdout'], []], stdout]})
                elif format == 'raw':
                    if self.stdout is not None:
                        nodes.append({'t': t_raw, 'c': ['markdown', self.stdout]})
                else:
                    raise ValueError
            elif display == 'stderr':
                if format in ('verbatim', 'autoverbatim'):
                    stderr = self.stderr
                    if (format == 'verbatim' and stderr is not None) or format == 'autoverbatim':
                        if stderr is None:
                            stderr = ''
                        if not self.inline:
                            stderr = stderr[:-1]
                        nodes.append({'t': t_code, 'c': [['', ['stderr'], []], stderr]})
                elif format == 'raw':
                    if self.stderr is not None:
                        nodes.append({'t': t_raw, 'c': ['markdown', self.stderr]})
                else:
                    raise ValueError
        return nodes




def walk_node_list(node_list, enumerate=enumerate, isinstance=isinstance):
    '''
    Walk all AST nodes in a list, recursively descending to walk all
    child nodes as well.  The walk function is written so that it is
    only ever called on lists, reducing recursion depth and reducing
    the number of times the walk function is called.  Thus, it is
    never called on `Str` nodes and other leaf nodes, which will
    typically make up the vast majority of nodes.

    DefinitionLists are handled specially to wrap terms in fake Plain
    nodes.  This simplifies processing.

    Returns nodes plus their parent lists with indices.
    '''
    for index, obj in enumerate(node_list):
        if isinstance(obj, list):
            yield from walk_node_list(obj)
        elif isinstance(obj, dict):
            yield (obj, node_list, index)
            obj_contents = obj.get('c', None)
            if obj_contents is not None and isinstance(obj_contents, list):
                if obj['t'] != 'DefinitionList':
                    yield from walk_node_list(obj_contents)
                else:
                    for elem in obj_contents:
                        term, definition = elem
                        yield ({'t': 'Plain', 'c': term}, elem, 0)
                        yield from walk_node_list(term)
                        yield from walk_node_list(definition)


def walk_node_list_less_note_contents(node_list, enumerate=enumerate, isinstance=isinstance):
    '''
    Like `walk_node_list()`, except that it will return a `Note` node
    but not iterate through it.  It can be useful to process `Note`
    contents separately since they are typically located in another
    part of the document and may contain block nodes.
    '''
    for index, obj in enumerate(node_list):
        if isinstance(obj, list):
            yield from walk_node_list_less_note_contents(obj)
        elif isinstance(obj, dict):
            yield (obj, node_list, index)
            if obj['t'] == 'Note':
                continue
            obj_contents = obj.get('c', None)
            if obj_contents is not None and isinstance(obj_contents, list):
                if obj['t'] != 'DefinitionList':
                    yield from walk_node_list_less_note_contents(obj_contents)
                else:
                    for elem in obj_contents:
                        term, definition = elem
                        yield ({'t': 'Plain', 'c': term}, elem, 0)
                        yield from walk_node_list_less_note_contents(term)
                        yield from walk_node_list_less_note_contents(definition)




class PandocConverter(Converter):
    '''
    Converter based on Pandoc (https://pandoc.org/).

    Pandoc is used to parse input into a JSON-based AST.  Code nodes in the
    AST are located, processed, and then replaced.  Next, the AST is converted
    back into the original input format (or possibly another format), and
    reparsed into a new AST.  This allows raw code output to be interpreted as
    markup.  Finally, the new AST can be converted into the output format.
    '''
    def __init__(self, *,
                 pandoc_path: Optional[Union[str, pathlib.Path]]=None,
                 pandoc_file_scope: Optional[bool]=False,
                 from_format_pandoc_extensions: Optional[str]=None,
                 scroll_sync: bool=False,
                 synctex: bool=False,
                 **kwargs):
        super().__init__(**kwargs)

        if pandoc_path is None:
            pandoc_path = pathlib.Path('pandoc')
        else:
            pandoc_path = pathlib.Path(pandoc_path)
            if self.expandvars:
                pandoc_path = pathlib.Path(os.path.expandvars(pandoc_path))
            if self.expanduser:
                pandoc_path = pandoc_path.expanduser()
        try:
            proc = subprocess.run([str(pandoc_path), '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except FileNotFoundError:
            raise RuntimeError('Pandoc path "{0}" does not exist'.format(pandoc_path))
        pandoc_version_match = re.search(rb'\d+\.\d+', proc.stdout)
        if not pandoc_version_match:
            raise RuntimeError('Could not determine Pandoc version from "{0} --version"; faulty Pandoc installation?'.format(pandoc_path))
        elif float(pandoc_version_match.group()) < 2.4:
            raise RuntimeError('Pandoc at "{0}" is version {1}, but >= 2.4 is required'.format(pandoc_path, float(pandoc_version_match.group())))
        self.pandoc_path = pandoc_path

        if not isinstance(pandoc_file_scope, bool):
            raise TypeError
        if pandoc_file_scope and self.cache_path is None:
            raise ValueError('pandoc_file_scope=True requires specifying cache_path')
        if pandoc_file_scope and kwargs.get('cross_source_sessions') is None:
            self.cross_source_sessions = False
        self.pandoc_file_scope = pandoc_file_scope
        if self.from_format == 'markdown' and not pandoc_file_scope and len(self.source_strings) > 1:
            # If multiple files are being passed to Pandoc for concatenated
            # processing, ensure sufficient whitespace to prevent elements in
            # different files from merging, and insert a comment to prevent
            # indented elements from merging.  This means that the original
            # sources cannot be passed to Pandoc directly.
            for n, source_string in enumerate(self.source_strings):
                if source_string[-1] == '\n':
                    source_string += '\n<!--codebraid.eof-->\n\n'
                else:
                    source_string += '\n\n<!--codebraid.eof-->\n\n'
                self.source_strings[n] = source_string
            self.concat_source_string = ''.join(self.source_strings)

        if (from_format_pandoc_extensions is not None and
                not isinstance(from_format_pandoc_extensions, str)):
            raise TypeError
        self.from_format_pandoc_extensions = from_format_pandoc_extensions or ''

        if not all(isinstance(x, bool) for x in (scroll_sync, synctex)):
            raise TypeError
        self.scroll_sync = scroll_sync
        if scroll_sync:
            raise NotImplementedError
        self.synctex = synctex
        if scroll_sync or synctex:
            self._io_map = True
        else:
            self._io_map = False

        self._asts = {}
        self._para_plain_source_name_node_line_number = []
        self._processed_markup = {}


    from_formats = set(['json', 'markdown'])
    to_formats = set(['json', 'markdown', 'html', 'latex'])
    multi_source_formats = set(['markdown'])


    # Node sets are based on pandocfilters
    # https://github.com/jgm/pandocfilters/blob/master/pandocfilters.py
    _null_block_node_type = set([''])
    _block_node_types = set(['Plain', 'Para', 'CodeBlock', 'RawBlock',
                             'BlockQuote', 'OrderedList', 'BulletList',
                             'DefinitionList', 'Header', 'HorizontalRule',
                             'Table', 'Div']) | _null_block_node_type


    def _run_pandoc(self, *,
                    from_format: str,
                    to_format: str,
                    from_format_pandoc_extensions: Optional[str]=None,
                    to_format_pandoc_extensions: Optional[str]=None,
                    file_scope=False,
                    input: Optional[str]=None,
                    input_paths: Optional[Union[pathlib.Path, Sequence[pathlib.Path]]]=None,
                    input_name: Optional[str]=None,
                    output_path: Optional[pathlib.Path]=None,
                    overwrite: bool=False,
                    standalone: bool=False,
                    trace: bool=False):
        '''
        Convert between formats using Pandoc.

        Communication with Pandoc is accomplished via pipes.
        '''
        if from_format not in self.from_formats:
            raise ValueError
        if to_format not in self.to_formats:
            raise ValueError
        if from_format_pandoc_extensions is None:
            from_format_pandoc_extensions = ''
        if to_format_pandoc_extensions is None:
            to_format_pandoc_extensions = ''
        if input and input_paths:
            raise TypeError
        if output_path is not None and not overwrite and output_path.exists():
            raise RuntimeError('Output path {0} exists, but overwrite=False'.format(output_path))

        cmd_list = [str(self.pandoc_path),
                    '--from', from_format + from_format_pandoc_extensions,
                    '--to', to_format + to_format_pandoc_extensions]
        if standalone:
            cmd_list.append('--standalone')
        if trace:
            cmd_list.append('--trace')
        if file_scope:
            cmd_list.append('--file-scope')
        if output_path:
            cmd_list.extend(['--output', output_path.as_posix()])
        if input_paths is not None:
            if isinstance(input_paths, pathlib.Path):
                cmd_list.append(input_paths.as_posix())
            else:
                cmd_list.extend([p.as_posix() for p in input_paths])

        if platform.system() == 'Windows':
            # Prevent console from appearing for an instant
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        else:
            startupinfo = None

        try:
            proc = subprocess.run(cmd_list,
                                  input=input,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT,
                                  encoding='utf8',
                                  startupinfo=startupinfo, check=True)
        except subprocess.CalledProcessError as e:
            if isinstance(input_paths, pathlib.Path) and input_name is None:
                input_name = input_paths.as_posix()
            if input_name is None:
                msg = 'Failed to run Pandoc:\n{0}'.format(e.stdout)
            else:
                msg = 'Failed to run Pandoc on source {0}:\n{1}'.format(input_name, e)
            raise PandocError(msg)
        return proc.stdout


    _walk_node_list = staticmethod(walk_node_list)
    _walk_node_list_less_note_contents = staticmethod(walk_node_list_less_note_contents)

    def _walk_ast(self, ast):
        '''
        Walk all nodes in AST.
        '''
        ast_root_node_list = ast['blocks']
        yield from self._walk_node_list(ast_root_node_list)

    def _walk_ast_less_note_contents(self, ast):
        '''
        Walk all nodes in AST, except those in Notes.
        '''
        ast_root_node_list = ast['blocks']
        yield from self._walk_node_list_less_note_contents(ast_root_node_list)


    @staticmethod
    def _io_map_span_node(source_name, line_number):
        '''
        Create an empty span node containing source name and line number as
        attributes.  This is used to attach source info to an AST location,
        and then track that location through AST transformations and
        conversions.
        '''
        span_node = {'t': 'Span',
                     'c': [
                              [
                                  '',  # id
                                  ['codebraid--temp'],  # classes
                                  [['trace', '{0}:{1}'.format(source_name, line_number)]]  # kv pairs
                              ],
                              []  # contents
                          ]
                    }
        return span_node

    @staticmethod
    def _freeze_raw_node(node, source_name, line_number,
                         type_translation_dict={'RawBlock': 'CodeBlock', 'RawInline': 'Code'}):
        '''
        Convert a raw node into a special code node.  This prevents the
        raw node from being prematurely interpreted/discarded during
        intermediate AST transformations.
        '''
        node['t'] = type_translation_dict[node['t']]
        raw_format, raw_content = node['c']
        node['c'] = [
                        [
                            '',  # id
                            ['codebraid--temp'],  # classes
                            [['format', raw_format], ['trace', '{0}:{1}'.format(source_name, line_number)]]  # kv pairs
                        ],
                        raw_content
                    ]

    @staticmethod
    def _thaw_raw_node(node, source_name, line_number,
                       type_translation_dict={'CodeBlock': 'RawBlock', 'Code': 'RawInline'}):
        '''
        Convert a special code node back into its original form as a raw node.
        '''
        node['t'] = type_translation_dict[node['t']]
        for k, v in node['c'][0][2]:
            if k == 'format':
                raw_format = k
                break
        raw_content = node['c'][1]
        node['c'] = [raw_format, raw_content]


    # Regex for footnotes.  While they shouldn't be indented, the Pandoc
    # parser does recognize them as long as the indentation is less than 4
    # spaces.  The restrictions on the footnote identifier are minimal.
    # https://pandoc.org/MANUAL.html#footnotes
    # https://github.com/jgm/pandoc/blob/master/src/Text/Pandoc/Readers/Markdown.hs
    _footnote_re = re.compile(r' {0,3}\[\^[^ \t]+?\]:')


    def _load_and_process_initial_ast(self, *,
                                      source_string, single_source_name=None,
                                      any=any, int=int, len=len, next=next):
        '''
        Convert source string into a Pandoc AST and perform a number of
        operations on the AST.
          * Assign source line numbers to some nodes.  This is needed later
            for syncing error messages and is also needed for SyncTeX when the
            output is LaTeX.
          * Locate all code nodes for further processing.
          * Convert all raw nodes into specially marked code nodes.  This
            allows them to pass through later Pandoc conversions without
            being lost or being interpreted before the final conversion.
            These special code nodes are converted back into raw nodes in the
            final AST before the final format conversion.
        '''
        # Currently, a brute-force approach is used to determine line numbers:
        # walk the AST, and then search the source each time a Str node,
        # etc. is encountered, starting at the end of the last string that was
        # located.  This isn't guaranteed to give correct results in all
        # cases, since not all AST information like attributes is used in
        # searching, but it will usually give exact results.  Since syncing is
        # a usability feature, an occasional small loss of precision isn't
        # critical.  Pandoc's `--trace` output is used to simplify this
        # process.  The trace can be used to determine the exact start of
        # root-level nodes, so it periodically corrects any errors that have
        # accumulated.  It can also be used to locate and skip over footnotes
        # and link definitions.  These do not appear in the AST except in
        # processed form, so they would be a primary source of sync errors
        # otherwise.
        #
        # The original attempt at implementing line number syncing used only
        # the trace plus AST, with no string searching.  That might have had
        # much better performance had it worked.  Unfortunately, the data in
        # the trace is typically only sufficient to give approximate locations
        # at best.  The Pandoc markdown extension markdown_in_html_blocks can
        # produce markdown nodes preceded and followed by HTML RawBlocks, but
        # only the following RawBlock appears in the trace.  Similarly, HTML
        # RawBlocks can appear out of order in the trace (at the end of the
        # current chunk, rather than where they appear relative to other leaf
        # nodes; for example, `<hr/>`).  The trace data for DefinitionList
        # follows different rules (line numbers typically larger by 1),
        # presumably because of how Pandoc's parsing works.  Empty elements,
        # such as might occur in an incomplete bullet list, are difficult to
        # deal with.

        # Convert source string to trace plus AST with Pandoc
        from_format_pandoc_extensions = self.from_format_pandoc_extensions
        if self.from_format == 'markdown':
            from_format_pandoc_extensions += '-latex_macros-smart'
        stdout_lines = self._run_pandoc(input=source_string,
                                        input_name=single_source_name,
                                        from_format=self.from_format,
                                        from_format_pandoc_extensions=from_format_pandoc_extensions,
                                        to_format='json',
                                        file_scope=self.pandoc_file_scope,
                                        trace=True).splitlines()
        raw_ast = stdout_lines.pop()
        try:
            ast = json.loads(raw_ast)
        except Exception as e:
            raise PandocError('Failed to load AST (incompatible Pandoc version?):\n{0}'.format(e))
        if not (isinstance(ast, dict) and
                'pandoc-api-version' in ast and 'blocks' in ast):
            raise PandocError('Incompatible Pandoc API version')
        if ast['pandoc-api-version'][0:2] != [1, 17]:
            warnings.warn('Pandoc API is {0}.{1}, but Codebraid is designed for 1.17; this might cause issues'.format(*ast['pandoc-api-version'][0:2]))
        self._asts[single_source_name] = ast

        source_string_lines = source_string.splitlines()

        # Process trace to determine location of footnotes and link
        # definitions, and mark those line numbers as invalid
        invalid_line_numbers = set()
        left_trace_type_slice_index = len('[trace] Parsed [')
        right_trace_chunk_slice_index = len(' of chunk')
        footnote_re = self._footnote_re
        in_footnote = False
        trace = ('', 1, False)  # (<node type>, <line number>, <in chunk>)
        try:
            for trace_line in stdout_lines:
                if trace_line.startswith('[WARNING]'):
                    continue
                last_trace_node_type, last_trace_line_number, last_trace_in_chunk = trace
                trace_line = trace_line[left_trace_type_slice_index:]
                trace_node_type, trace_line = trace_line.split(' ', 1)
                trace_node_type = trace_node_type.rstrip(']')
                if trace_line.endswith('chunk'):
                    trace_in_chunk = True
                    trace_line_number = int(trace_line.split('at line ', 1)[1][:-right_trace_chunk_slice_index])
                else:
                    trace_in_chunk = False
                    trace_line_number = int(trace_line.split('at line ', 1)[1])
                trace = (trace_node_type, trace_line_number, trace_in_chunk)
                # Since footnotes must be at root level in the AST, line
                # numbers are guaranteed to be correct for their start
                # locations (no chunk numbering involved), so this will always
                # detect and skip them correctly.  There shouldn't be any
                # indentation before the `[^<identifier>]:`, but a regex is
                # used to cover all cases that the parser will accept.  A
                # footnote is always followed by a Null at root level.
                if (trace_in_chunk and not last_trace_in_chunk and
                        footnote_re.match(source_string_lines[last_trace_line_number-1])):
                    in_footnote = True
                    invalid_line_number = last_trace_line_number
                    continue
                if in_footnote:
                    if trace_in_chunk:
                        continue
                    in_footnote = False
                    while invalid_line_number < trace_line_number:
                        invalid_line_numbers.add(invalid_line_number)
                        invalid_line_number += 1
                # Link definitions must be at root level and produce Nulls
                if not trace_node_type and not last_trace_in_chunk and not trace_in_chunk:
                    invalid_line_number = last_trace_line_number
                    while invalid_line_number < trace_line_number:
                        invalid_line_numbers.add(invalid_line_number)
                        invalid_line_number += 1
        except Exception as e:
            raise PandocError('Incompatible Pandoc version or trace; cannot parse trace format:\n{0}'.format(e))

        # Iterator for source lines and line numbers that skips invalid lines
        if self.pandoc_file_scope or len(self.source_strings) == 1:
            source_name_line_and_number_iter = ((single_source_name, line, n+1) for (n, line) in enumerate(source_string_lines) if n+1 not in invalid_line_numbers)
        else:
            def make_source_name_line_and_number_iter():
                line_and_concat_line_number_iter = ((line, n+1) for (n, line) in enumerate(source_string_lines))
                for source_name, source_string in zip(self.source_names, self.source_strings):
                    for line_number in range(1, source_string.count('\n')+1):
                        line, concat_line_number = next(line_and_concat_line_number_iter)
                        if concat_line_number in invalid_line_numbers:
                            continue
                        yield (source_name, line, line_number)
            source_name_line_and_number_iter = make_source_name_line_and_number_iter()


        # Walk AST to associate line numbers with AST nodes
        para_plain_source_name_node_line_number = self._para_plain_source_name_node_line_number
        source_name, line, line_number = next(source_name_line_and_number_iter)
        line_index = 0
        block_node_types = self._block_node_types
        freeze_raw_node = self._freeze_raw_node
        for node_tuple in self._walk_ast_less_note_contents(ast):
            node, parent_node_list, parent_node_list_index = node_tuple
            node_type = node['t']
            if node_type == 'Str':
                node_contents = node['c']
                line_index = line.find(node_contents, line_index)
                if line_index >= 0:
                    line_index += len(node_contents)
                else:
                    source_name, line, line_number = next(source_name_line_and_number_iter)
                    line_index = line.find(node_contents)
                    if line_index < 0:
                        while line_index < 0:
                            source_name, line, line_number = next(source_name_line_and_number_iter)
                            line_index = line.find(node_contents)
                    line_index += len(node_contents)
                if para_plain_node is not None:
                    para_plain_source_name_node_line_number.append((source_name, para_plain_node, line_number))
                    para_plain_node = None
            elif node_type in ('Code', 'RawInline'):
                node_contents = node['c'][1]
                last_line_index = line_index
                line_index = line.find(node_contents, line_index)
                if line_index >= 0:
                    line_index += len(node_contents)
                else:
                    line_index = last_line_index
                    for node_contents_elem in node_contents.split(' '):
                        line_index = line.find(node_contents_elem, line_index)
                        if line_index >= 0:
                            line_index += len(node_contents_elem)
                        else:
                            source_name, line, line_number = next(source_name_line_and_number_iter)
                            line_index = line.find(node_contents_elem)
                            if line_index < 0:
                                while line_index < 0:
                                    source_name, line, line_number = next(source_name_line_and_number_iter)
                                    line_index = line.find(node_contents_elem)
                            line_index += len(node_contents_elem)
                if node_type == 'Code':
                    if any(c == 'cb' or c.startswith('cb.') for c in node['c'][0][1]):  # 'cb.*' in classes
                        code_chunk = PandocCodeChunk(node, parent_node_list, parent_node_list_index,
                                                     source_name, line_number)
                        self.code_chunks.append(code_chunk)
                else:
                    freeze_raw_node(node, source_name, line_number)
                if para_plain_node is not None:
                    para_plain_source_name_node_line_number.append((source_name, para_plain_node, line_number))
                    para_plain_node = None
            elif node_type == 'CodeBlock':
                node_contents_lines = node['c'][1].splitlines()
                for node_contents_line in node_contents_lines:
                    if node_contents_line not in line:
                        # Move forward a line at a time until match
                        source_name, line, line_number = next(source_name_line_and_number_iter)
                        if node_contents_line not in line:
                            while node_contents_line not in line:
                                source_name, line, line_number = next(source_name_line_and_number_iter)
                    # Once there's a match, move forward one line per loop
                    source_name, line, line_number = next(source_name_line_and_number_iter)
                line_index = 0
                if any(c == 'cb' or c.startswith('cb.') for c in node['c'][0][1]):  # 'cb.*' in classes
                    code_chunk = PandocCodeChunk(node, parent_node_list, parent_node_list_index,
                                                 source_name, line_number - len(node_contents_lines))
                    self.code_chunks.append(code_chunk)
                para_plain_node = None
            elif node_type == 'RawBlock':
                # Note that HTML RawBlock can be inline, so use `.find()`
                # rather than `in` to guarantee results.  The
                # `len(node_contents_line) or 1` guarantees that multiple
                # empty `node_contents_line` won't keep matching the same
                # `line`.
                node_format, node_contents = node['c']
                node_contents_lines = node_contents.splitlines()
                for node_contents_line in node_contents_lines:
                    line_index = line.find(node_contents_line)
                    if line_index >= 0:
                        line_index += len(node_contents_line) or 1
                    else:
                        source_name, line, line_number = next(source_name_line_and_number_iter)
                        line_index = line.find(node_contents_line)
                        if line_index < 0:
                            while line_index < 0:
                                source_name, line, line_number = next(source_name_line_and_number_iter)
                                line_index = line.find(node_contents_line)
                        line_index += len(node_contents_line) or 1
                if node_format == 'html' and node_contents == '<!--codebraid.eof-->':
                    node['t'] = 'Null'
                    del node['c']
                else:
                    freeze_raw_node(node, source_name, line_number - len(node_contents_lines) + 1)
                para_plain_node = None
            elif node_type == 'Note':
                for note_node_tuple in self._walk_node_list(node['c']):
                    note_node, note_parent_node_list, note_parent_node_list_index = note_node_tuple
                    note_node_type = node['t']
                    if note_node_type in ('Code', 'CodeBlock'):
                        if any(c == 'cb' or c.startswith('cb.') for c in node['c'][0][1]):  # 'cb.*' in classes
                            code_chunk = PandocCodeChunk(note_node, note_parent_node_list, note_parent_node_list_index,
                                                         source_name, line_number)
                            self.code_chunks.append(code_chunk)
                    elif note_node_type in ('RawInline', 'RawBlock'):
                        freeze_raw_node(note_node, source_name, line_number)
            elif node_type in ('Para', 'Plain'):
                para_plain_node = node
            elif node_type in block_node_types:
                para_plain_node = None


    def _extract_code_chunks(self):
        if self.pandoc_file_scope or len(self.source_strings) == 1:
            for source_string, source_name in zip(self.source_strings, self.source_names):
                self._load_and_process_initial_ast(source_string=source_string, single_source_name=source_name)
        else:
            self._load_and_process_initial_ast(source_string=self.concat_source_string)


    def _postprocess_code_chunks(self):
        for code_chunk in reversed(self.code_chunks):
            # Substitute code output into AST in reversed order to preserve
            # indices
            index = code_chunk.parent_node_list_index
            code_chunk.parent_node_list[index:index+1] = code_chunk.output_nodes()
        if self._io_map:
            for source_name, node, line_number in self._para_plain_source_name_node_line_number:
                node['c'].insert(0, self._io_map_span_node(source_name, line_number))
        for source_name, ast in self._asts.items():
            self._processed_markup[source_name] = self._run_pandoc(input=json.dumps(ast),
                                                                   from_format='json',
                                                                   to_format='markdown',
                                                                   to_format_pandoc_extensions=self.from_format_pandoc_extensions+'-latex_macros-smart',
                                                                   standalone=True)

    def convert(self, *, to_format, to_format_pandoc_extensions=None, standalone=False):
        if to_format not in self.to_formats:
            raise ValueError
        if not self.pandoc_file_scope or len(self.source_strings) == 1:
            for markup in self._processed_markup.values():
                converted = self._run_pandoc(input=markup,
                                             from_format='markdown',
                                             from_format_pandoc_extensions=self.from_format_pandoc_extensions,
                                             to_format=to_format,
                                             to_format_pandoc_extensions=to_format_pandoc_extensions,
                                             standalone=standalone)
        else:
            # No need to check for cache_path is not None; done in __init__
            tempfile_paths = []
            for n, markup in enumerate(self._processed_markup.values()):
                tempfile_path = self.cache_temp_path / 'codebraid_intermediate_{0}.txt'.format(n)
                tempfile_path.write_text(markup, encoding='utf8')
                tempfile_paths.append(tempfile_path)
            converted = self._run_pandoc(input_paths=tempfile_paths,
                                         from_format='markdown',
                                         from_format_pandoc_extensions=self.from_format_pandoc_extensions,
                                         to_format=to_format,
                                         to_format_pandoc_extensions=to_format_pandoc_extensions,
                                         standalone=standalone)
            for tempfile_path in tempfile_paths:
                tempfile_path.unlink()
        return converted