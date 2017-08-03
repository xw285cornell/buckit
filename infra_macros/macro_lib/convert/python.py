#!/usr/bin/env python2

# Copyright 2016-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import collections
from distutils.version import LooseVersion
import os
import operator
import pipes

from . import base
from .base import ThirdPartyRuleTarget
from ..rule import Rule


INTERPS = [
    ('interp', 'libfb.py.python_interp', '//libfb/py:python_interp'),
    ('ipython', 'libfb.py.ipython_interp', '//libfb/py:ipython_interp'),
    ('vs_debugger', 'libfb.py.vs_debugger', '//libfb/py:vs_debugger'),
]


GEN_SRCS_LINK = 'https://fburl.com/203312823'


MANIFEST_TEMPLATE = """\
import sys


class Manifest(object):

    def __init__(self):
        self._modules = None

    @property
    def modules(self):
        if self._modules is None:
            import os, sys
            modules = set()
            for root, dirs, files in os.walk(sys.path[0]):
                rel_root = os.path.relpath(root, sys.path[0])
                for name in files:
                    base, ext = os.path.splitext(name)
                    if ext in ('.py', '.pyc', '.pyo', '.so'):
                        modules.add(
                            os.path.join(rel_root, base).replace(os.sep, '.'))
            self._modules = sorted(modules)
        return self._modules

    fbmake = {{
        {fbmake}
    }}


sys.modules[__name__] = Manifest()
"""


class PythonConverter(base.Converter):

    RULE_TYPE_MAP = {
        'python_library': 'python_library',
        'python_binary': 'python_binary',
        'python_unittest': 'python_test',
    }

    def __init__(self, context, rule_type):
        super(PythonConverter, self).__init__(context)
        self._rule_type = rule_type

    def get_fbconfig_rule_type(self):
        return self._rule_type

    def get_buck_rule_type(self):
        return self.RULE_TYPE_MAP[self._rule_type]

    def is_binary(self):
        return self.get_fbconfig_rule_type() in (
            'python_binary',
            'python_unittest',
        )

    def convert_srcs_to_dict(self, srcs):
        if isinstance(srcs, dict):
            return {v: k for k, v in srcs.iteritems()}
        return {s: s for s in srcs}

    def convert_gen_srcs_to_dict(self, srcs):
        """
        Convert the `gen_srcs` parameter, given as either a list or dict
        mapping sources to destinations, into a Buck-compatible dict mapping
        destinations to sources.
        """

        out_srcs = {}

        # If `gen_srcs` is already in dict form, then we just need to invert
        # the map, since Buck uses a `dst->src` mapping, while fbconfig uses
        # a `src->dst` mapping.
        if isinstance(srcs, dict):
            for src, dst in srcs.iteritems():
                out_srcs[dst] = src

        # Otherwise, `gen_srcs` is a list, and we need to convert it into a
        # `dict`, which means we need to infer a proper name for it below to
        # use as the destination.
        else:
            for src in srcs:

                # If the source comes from a `custom_rule`/`genrule`, and the
                # user used the `=` notation which encodes the sources "name",
                # we can extract and use that.
                if '=' in src:
                    name = src.rsplit('=', 1)[1]
                    out_srcs[name] = src
                    continue

                # Otherwise, we don't have a good way of deducing the name.
                # This actually looks to be pretty rare, so just throw a useful
                # error prompting the user to use the `=` notation above, or
                # switch to an explicit `dict`.
                raise ValueError(
                    'parameter `gen_srcs`: cannot infer a "name" to use for '
                    '`{}`. If this is an output from a `custom_rule`, '
                    'consider using the `<rule-name>=<out>` notation instead. '
                    'Otherwise, please specify this parameter as `dict` '
                    'mapping sources to explicit "names" (see {} for details).'
                    .format(src, GEN_SRCS_LINK))

        # Do a final pass to verify that all sources in `gen_srcs` are rule
        # references.
        for src in out_srcs.itervalues():
            if src[0] not in '@:' and src.count(':') != 2:
                raise ValueError(
                    'parameter `gen_srcs`: `{}` must be a reference to rule '
                    'that generates a source (e.g. `@/foo:bar`, `:bar`) '
                    ' (see {} for details).'
                    .format(src, GEN_SRCS_LINK))

        return out_srcs

    def parse_constraint(self, constraint):
        """
        Parse the given constraint into callable which tests a `LooseVersion`
        object.
        """

        if constraint is None:
            return lambda other: True

        # complex Constraints are silly, we only have py2 and py3
        if constraint in (2, '2'):
            constraint = self.get_py2_version()
            op = operator.eq
        elif constraint in (3, '3'):
            constraint = self.get_py3_version()
            op = operator.eq
        elif constraint.startswith('<='):
            constraint = constraint[2:].lstrip()
            op = operator.le
        elif constraint.startswith('>='):
            constraint = constraint[2:].lstrip()
            op = operator.ge
        elif constraint.startswith('<'):
            constraint = constraint[1:].lstrip()
            op = operator.lt
        elif constraint.startswith('='):
            constraint = constraint[1:].lstrip()
            op = operator.eq
        elif constraint.startswith('>'):
            constraint = constraint[1:].lstrip()
            op = operator.gt
        else:
            op = operator.eq

        return lambda other: op(other, LooseVersion(constraint))

    def matches_py2(self, constraint):
        matches = self.parse_constraint(constraint)
        return matches(LooseVersion(self.get_py2_version()))

    def matches_py3(self, constraint):
        matches = self.parse_constraint(constraint)
        return matches(LooseVersion(self.get_py3_version()))

    def get_python_version(self, constraint):
        if self.matches_py3(constraint):
            return self.get_py3_version()
        if self.matches_py2(constraint):
            return self.get_py2_version()
        raise ValueError('invalid python constraint: {!r}'.format(constraint))

    def get_interpreter(self, platform, python_version):
        return '/usr/local/fbcode/{}/bin/python{}'.format(
            platform,
            python_version[:3])

    def get_version_universe(self, python_version):
        return super(PythonConverter, self).get_version_universe(
            [('python', python_version)])

    def convert_external_build_target(self, target):
        """
        Convert the given build target reference from an externel dep TARGETS
        file reference from the context of a python library/binary.
        """

        return super(PythonConverter, self).convert_external_build_target(
            target,
            lang_suffix='-py')

    def convert_needed_coverage_spec(self, base_path, spec):
        if len(spec) != 2:
            raise ValueError(
                'parameter `needed_coverage`: `{}` must have exactly 2 '
                'elements, a ratio and a target.'
                .format(spec))

        ratio, target = spec
        if '=' not in target:
            return (
                ratio,
                self.convert_build_target(base_path, target))
        target, path = target.rsplit('=', 1)
        return (ratio, self.convert_build_target(base_path, target), path)

    def get_python_build_info(
            self,
            base_path,
            name,
            main_module,
            platform,
            python_version):
        """
        Return the build info attributes to install for python rules.
        """

        py_build_info = collections.OrderedDict()

        py_build_info['main_module'] = main_module

        interp = self.get_interpreter(platform, python_version)
        py_build_info['python_home'] = os.path.dirname(os.path.dirname(interp))
        py_build_info['python_command'] = pipes.quote(interp)

        # Include the standard build info, converting the keys to the names we
        # use for python.
        key_mappings = {
            'package_name': 'package',
            'package_version': 'version',
            'rule': 'build_rule',
            'rule_type': 'build_rule_type',
        }
        build_info = (
            self.get_build_info(
                base_path,
                name,
                self.get_fbconfig_rule_type(),
                platform))
        for key, val in build_info.iteritems():
            py_build_info[key_mappings.get(key, key)] = val

        return py_build_info

    def generate_manifest(
            self,
            base_path,
            name,
            main_module,
            platform,
            python_version):
        """
        Build the rules that create the `__manifest__` module.
        """

        rules = []

        build_info = (
            self.get_python_build_info(
                base_path,
                name,
                main_module,
                platform,
                python_version))
        manifest = MANIFEST_TEMPLATE.format(
            fbmake='\n        '.join(
                '{!r}: {!r},'.format(k, v) for k, v in build_info.iteritems()))

        manifest_name = name + '-manifest'
        manifest_attrs = collections.OrderedDict()
        manifest_attrs['name'] = manifest_name
        manifest_attrs['out'] = name + '-__manifest__.py'
        manifest_attrs['cmd'] = (
            'echo -n {} > $OUT'.format(pipes.quote(manifest)))
        rules.append(Rule('genrule', manifest_attrs))

        manifest_lib_name = name + '-manifest-lib'
        manifest_lib_attrs = collections.OrderedDict()
        manifest_lib_attrs['name'] = manifest_lib_name
        manifest_lib_attrs['base_module'] = ''
        manifest_lib_attrs['srcs'] = {'__manifest__.py': ':' + manifest_name}
        rules.append(Rule('python_library', manifest_lib_attrs))

        return manifest_lib_name, rules

    def get_par_build_args(
            self,
            base_path,
            name,
            platform,
            argcomplete=None,
            strict_tabs=None,
            compile=None,
            par_style=None,
            strip_libpar=None,
            needed_coverage=None,
            python=None):
        """
        Return the arguments we need to pass to the PAR builder wrapper.
        """

        build_args = []

        if self._context.config.use_custom_par_args:
            # Arguments that we wanted directly threaded into `make_par`.
            passthrough_args = []
            if argcomplete is True:
                passthrough_args.append('--argcomplete')
            if strict_tabs is False:
                passthrough_args.append('--no-strict-tabs')
            if compile is False:
                passthrough_args.append('--no-compile')
                passthrough_args.append('--store-source')
            elif compile == 'with-source':
                passthrough_args.append('--store-source')
            elif compile is not True and compile is not None:
                raise Exception(
                    'Invalid value {} for `compile`, must be True, False, '
                    '"with-source", or None (default)'.format(compile)
                )
            if par_style is not None:
                passthrough_args.append('--par-style=' + par_style)
            if needed_coverage is not None or self._context.coverage:
                passthrough_args.append('--store-source')
            passthrough_args.append(
                '--build-rule-name=fbcode:{}:{}'.format(base_path, name))
            if self._context.mode.startswith('opt'):
                passthrough_args.append('--optimize')
            build_args.extend(['--passthrough=' + a for a in passthrough_args])

            # Arguments for stripping libomnibus. dbg builds should never strip.
            if self._context.mode.startswith('dbg') or strip_libpar is False:
                build_args.append('--omnibus-debug-info=separate')
            elif strip_libpar == 'extract':
                build_args.append('--omnibus-debug-info=extract')
            else:
                build_args.append('--omnibus-debug-info=strip')

            # Add arguments to populate build info.
            build_args.append('--build-info-mode=' + self._context.mode)
            build_args.append('--build-info-platform=' + platform)

            # Set an explicit python interpreter.
            if python is not None:
                build_args.append('--python-override=' + python)

        return build_args

    def should_generate_interp_rules(self):
        """
        Return whether we should generate the interp helpers.
        """

        # Our current implementation of the interp helpers is costly when using
        # omnibus linking, so only generate these by default for `dev` mode or
        # if explicitly set via config.
        return self.read_bool(
            'python',
            'helpers',
            self._context.mode.startswith('dev'))

    def convert_interp_rules(
            self,
            name,
            platform,
            python_version,
            python_platform,
            deps,
            platform_deps,
            preload_deps):
        """
        Generate rules to build intepreter helpers.
        """

        rules = []

        for interp, interp_main_module, interp_dep in INTERPS:
            attrs = collections.OrderedDict()
            attrs['name'] = name + '-' + interp
            attrs['main_module'] = interp_main_module
            attrs['cxx_platform'] = platform
            attrs['platform'] = python_platform
            attrs['version_universe'] = (
                self.get_version_universe(python_version))
            attrs['deps'] = [interp_dep] + deps
            attrs['platform_deps'] = platform_deps
            attrs['preload_deps'] = preload_deps
            attrs['package_style'] = 'inplace'
            rules.append(Rule('python_binary', attrs))

        return rules

    def get_preload_deps(self, allocator):
        """
        Add C/C++ deps which need to preloaded by Python binaries.
        """

        deps = []

        # If we're using sanitizers, add the dep on the sanitizer-specific
        # support library.
        if self._context.sanitizer is not None:
            sanitizer = base.SANITIZERS[self._context.sanitizer]
            deps.append('//tools/build/sanitizers:{}-py'.format(sanitizer))

        # If we're using an allocator, and not a sanitizer, add the allocator-
        # specific deps.
        if allocator is not None and self._context.sanitizer is None:
            deps.extend(self.get_allocators()[allocator])
        return deps

    def get_ldflags(self, base_path, name, strip_libpar=True):
        """
        Return ldflags to use when linking omnibus libraries in python binaries.
        """

        # We override stripping for python binaries unless we're in debug mode
        # (which doesn't get stripped by default).  If either `strip_libpar`
        # is set or any level of stripping is enabled via config, we do full
        # stripping.
        strip_mode = self.get_strip_mode(base_path, name)
        if (not self._context.mode.startswith('dbg') and
                (strip_mode != 'none' or strip_libpar is True)):
            strip_mode = 'full'

        return super(PythonConverter, self).get_ldflags(
            base_path,
            name,
            self.get_fbconfig_rule_type(),
            strip_mode=strip_mode)

    def get_package_style(self):
        return self.read_choice(
            'python',
            'package_style',
            ['inplace', 'standalone'])

    def create_library(
        self,
        base_path,
        name,
        base_module=None,
        srcs=(),
        versioned_srcs=(),
        gen_srcs=(),
        deps=[],
        tests=[],
        external_deps=[],
    ):
        attributes = collections.OrderedDict()
        attributes['name'] = name

        # Normalize all the sources from the various parameters.
        new_srcs = {}
        new_srcs.update(self.convert_srcs_to_dict(srcs))
        new_srcs.update(self.convert_gen_srcs_to_dict(gen_srcs))
        srcs = new_srcs

        # Contains a mapping of platform name to sources to use for that
        # platform.
        all_versioned_srcs = []

        # If we're TP project, install all sources via the `versioned_srcs`
        # parameter.
        if self.is_tp2(base_path):

            # TP2 projects have multiple "pre-built" source dirs, so we install
            # them via the `versioned_srcs` parameter along with the versions
            # of deps that was used to build them, so that Buck can select the
            # correct one based on version resolution.
            project_builds = self.get_tp2_project_builds(base_path)
            for build in project_builds.values():
                build_srcs = [srcs]
                if versioned_srcs:
                    py_vers = build.versions['python']
                    build_srcs.extend(
                        [self.convert_srcs_to_dict(vs)
                         for pv, vs in versioned_srcs if pv[:3] == py_vers])
                vsrc = collections.OrderedDict()
                for build_src in build_srcs:
                    vsrc.update(
                        {d: os.path.join(build.subdir, s)
                         for d, s in build_src.items()})
                all_versioned_srcs.append((build.project_deps, vsrc))

            # Reset `srcs`, since we're using `versioned_srcs`.
            srcs = {}

        # If we're an fbcode project, then keep the regular sources parameter
        # and only use the `versioned_srcs` parameter for the input parameter
        # of the same name.
        else:
            py2_srcs = {}
            py3_srcs = {}
            for constraint, vsrcs in versioned_srcs:
                vsrcs = self.convert_srcs_to_dict(vsrcs)
                if self.matches_py2(constraint):
                    py2_srcs.update(vsrcs)
                if self.matches_py3(constraint):
                    py3_srcs.update(vsrcs)
            if py2_srcs or py3_srcs:
                py = self.get_tp2_project_target('python')
                py2 = self.get_py2_version()
                py3 = self.get_py3_version()
                platforms = (
                    self.get_platforms()
                    if not self.is_tp2(base_path)
                    else [self.get_tp2_platform(base_path)])
                all_versioned_srcs.append(
                    ({self.get_dep_target(py, platform=p): py2
                      for p in platforms},
                     py2_srcs))
                all_versioned_srcs.append(
                    ({self.get_dep_target(py, platform=p): py3
                      for p in platforms},
                     py3_srcs))

        if base_module is not None:
            attributes['base_module'] = base_module

        if srcs:
            # Need to split the srcs into srcs & resources as Buck
            # expects all test srcs to be python modules.
            if self.is_test(self.get_buck_rule_type()):
                attributes['srcs'] = self.convert_source_map(
                    base_path,
                    {k: v for k, v in srcs.iteritems() if k.endswith('.py')})
                attributes['resources'] = self.convert_source_map(
                    base_path,
                    {k: v for k, v in srcs.iteritems()
                        if not k.endswith('.py')})
            else:
                attributes['srcs'] = self.convert_source_map(base_path, srcs)

        # Emit platform-specific sources.  We split them between the
        # `platform_srcs` and `platform_resources` parameter based on their
        # extension, so that directories with only resources don't end up
        # creating stray `__init__.py` files for in-place binaries.
        if all_versioned_srcs:
            out_versioned_srcs = []
            out_versioned_resources = []
            for vcollection, ver_srcs in all_versioned_srcs:
                out_srcs = collections.OrderedDict()
                out_resources = collections.OrderedDict()
                for dst, src in (
                        self.convert_source_map(base_path, ver_srcs).items()):
                    if dst.endswith('.py') or dst.endswith('.so'):
                        out_srcs[dst] = src
                    else:
                        out_resources[dst] = src
                out_versioned_srcs.append((vcollection, out_srcs))
                out_versioned_resources.append((vcollection, out_resources))
            if out_versioned_srcs:
                attributes['versioned_srcs'] = out_versioned_srcs
            if out_versioned_resources:
                attributes['versioned_resources'] = out_versioned_resources

        dependencies = []
        if self.is_tp2(base_path):
            dependencies.append(self.get_tp2_project_dep(base_path))
        for target in deps:
            dependencies.append(
                self.convert_build_target(base_path, target))
        if dependencies:
            attributes['deps'] = dependencies

        attributes['tests'] = tests

        if external_deps:
            attributes['platform_deps'] = (
                self.format_platform_deps(
                    self.to_platform_deps(
                        [self.normalize_external_dep(d, lang_suffix='-py')
                         for d in external_deps])))

        return Rule('python_library', attributes)

    def create_binary(
        self,
        base_path,
        name,
        library,
        tests=[],
        py_version=None,
        main_module=None,
        rule_type=None,
        strip_libpar=True,
        tags=(),
        lib_dir=None,
        par_style=None,
        emails=None,
        needed_coverage=None,
        argcomplete=None,
        strict_tabs=None,
        compile=None,
        args=None,
        env=None,
        python=None,
        allocator=None,
        check_types=False,
    ):
        rules = []
        dependencies = []
        platform_deps = []
        preload_deps = []
        platform = self.get_platform(base_path)
        python_version = self.get_python_version(py_version)
        python_platform = self.get_python_platform(platform, python_version)

        if allocator is None:
            # Default gcc-5 platforms to jemalloc (as per S146810).
            if self.get_tool_version(platform, 'gcc') >= LooseVersion('5'):
                allocator = 'jemalloc'
            else:
                allocator = 'malloc'

        attributes = collections.OrderedDict()
        attributes['name'] = name

        if not rule_type:
            rule_type = self.get_buck_rule_type()

        # Add the library to our exported rules.
        rules.append(library)

        # If this is a test, we need to merge the library rule into this
        # one and inherit its deps.
        if self.is_test(rule_type):
            for param in ('versioned_srcs', 'srcs', 'resources', 'base_module'):
                val = library.attributes.get(param)
                if val is not None:
                    attributes[param] = val
            dependencies.extend(library.attributes.get('deps', []))
            platform_deps.extend(library.attributes.get('platform_deps', []))

            # Add the "coverage" library as a dependency for all python tests.
            platform_deps.extend(
                self.format_platform_deps(
                    self.to_platform_deps(
                        [ThirdPartyRuleTarget('coverage', 'coverage-py')])))

        # Otherwise, this is a binary, so just the library portion as a dep.
        else:
            dependencies.append(':' + library.attributes['name'])

        # Sanitize the main module, so that it's a proper module reference.
        if main_module is not None:
            main_module = main_module.replace('/', '.')
            if main_module.endswith('.py'):
                main_module = main_module[:-3]
            attributes['main_module'] = main_module
        elif self.is_test(rule_type):
            main_module = '__fb_test_main__'
            attributes['main_module'] = main_module

        # Add in the PAR build args.
        build_args = (
            self.get_par_build_args(
                base_path,
                name,
                platform,
                argcomplete=argcomplete,
                strict_tabs=strict_tabs,
                compile=compile,
                par_style=par_style,
                strip_libpar=strip_libpar,
                needed_coverage=needed_coverage,
                python=python))
        if build_args:
            attributes['build_args'] = build_args

        # Add any special preload deps.
        preload_deps.extend(self.get_preload_deps(allocator))

        # Add the C/C++ build info lib to preload deps.
        cxx_build_info, cxx_build_info_rules = (
            self.create_cxx_build_info_rule(
                base_path,
                name,
                self.get_fbconfig_rule_type(),
                platform,
                static=False))
        preload_deps.append(self.get_dep_target(cxx_build_info))
        rules.extend(cxx_build_info_rules)

        # Provide a standard set of backport deps to all binaries
        platform_deps.extend(
            self.format_platform_deps(
                self.to_platform_deps(
                    [ThirdPartyRuleTarget('typing', 'typing-py'),
                     ThirdPartyRuleTarget('python-future', 'python-future-py')])))

        # Add in a specialized manifest when building inplace binaries.
        #
        # TODO(#11765906):  We shouldn't need to create this manifest rule for
        # standalone binaries.  However, since target determinator runs in dev
        # mode, we sometimes pass these manifest targets in the explicit target
        # list into `opt` builds, which then fails with a missing build target
        # error.  So, for now, just always generate the manifest library, but
        # only use it when building inplace binaries.
        manifest_name, manifest_rules = (
            self.generate_manifest(
                base_path,
                name,
                main_module,
                platform,
                python_version))
        rules.extend(manifest_rules)
        if self.get_package_style() == 'inplace':
            dependencies.append(':' + manifest_name)

        attributes['cxx_platform'] = platform
        attributes['platform'] = python_platform
        attributes['version_universe'] = (
            self.get_version_universe(python_version))
        attributes['linker_flags'] = (
            self.get_ldflags(base_path, name, strip_libpar=strip_libpar))

        if self.is_test(rule_type):
            attributes['labels'] = self.convert_labels('python', *tags)

        attributes['tests'] = tests

        if args:
            attributes['args'] = self.convert_args_with_macros(base_path, args)

        if env:
            attributes['env'] = self.convert_env_with_macros(base_path, env)

        if emails:
            attributes['contacts'] = emails

        if preload_deps:
            attributes['preload_deps'] = preload_deps

        if needed_coverage:
            attributes['needed_coverage'] = [
                self.convert_needed_coverage_spec(base_path, s)
                for s in needed_coverage
            ]

        # Generate the interpreter helpers, and add them to our deps. Note that
        # we must do this last, so that the interp rules get the same deps as
        # the main binary which we've built up to this point.
        if self.should_generate_interp_rules():
            interp_deps = list(dependencies)
            if self.is_test(rule_type):
                rules.extend(self.gen_test_modules(base_path, library))
                interp_deps.append(
                    ':{}-testmodules-lib'.format(library.attributes['name'])
                )
            interp_rules = (
                self.convert_interp_rules(
                    name,
                    platform,
                    python_version,
                    python_platform,
                    interp_deps,
                    platform_deps,
                    preload_deps))
            rules.extend(interp_rules)
            dependencies.extend(
                ':' + r.attributes['name'] for r in interp_rules)
        if check_types:
            if not self.matches_py3(python_version):
                raise ValueError(
                    'parameter `check_types` is only supported on Python 3.'
                )
            rules.append(
                self.create_typecheck(
                    name,
                    main_module,
                    platform,
                    python_platform,
                    library,
                    dependencies,
                    platform_deps,
                    preload_deps,
                ),
            )

        if self.is_test(rule_type):
            if not dependencies:
                dependencies = []
            dependencies.append('//python:fbtestmain')

        if dependencies:
            attributes['deps'] = dependencies

        if platform_deps:
            attributes['platform_deps'] = platform_deps

        return [Rule(rule_type, attributes)] + rules

    def convert(
        self,
        base_path,
        name=None,
        py_version=None,
        base_module=None,
        main_module=None,
        strip_libpar=True,
        srcs=(),
        versioned_srcs=(),
        tags=(),
        gen_srcs=(),
        deps=[],
        tests=[],
        lib_dir=None,
        par_style=None,
        emails=None,
        external_deps=[],
        needed_coverage=None,
        output_subdir=None,
        argcomplete=None,
        strict_tabs=None,
        compile=None,
        args=None,
        env=None,
        python=None,
        allocator=None,
        check_types=False,
    ):
        # For libraries, create the library and return it.
        if not self.is_binary():
            library = self.create_library(
                base_path,
                name,
                base_module=base_module,
                srcs=srcs,
                versioned_srcs=versioned_srcs,
                gen_srcs=gen_srcs,
                deps=deps,
                tests=tests,
                external_deps=external_deps,
            )
            return [library]

        # For binary rules, create a separate library containing the sources.
        # This will be added as a dep for python binaries and merged in for
        # python tests.
        if not isinstance(py_version, list):
            versions = {py_version: name}
        else:
            versions = {}
            for py_ver in py_version:
                python_version = self.get_python_version(py_ver)
                new_name = name + '-' + python_version
                versions[py_ver] = new_name
        rules = []
        rule_names = []
        for py_ver, py_name in sorted(versions.items()):
            library = self.create_library(
                base_path,
                py_name + '-library',
                base_module=base_module,
                srcs=srcs,
                versioned_srcs=versioned_srcs,
                gen_srcs=gen_srcs,
                deps=deps,
                tests=tests,
                external_deps=external_deps,
            )
            one_set_rules = self.create_binary(
                base_path,
                py_name,
                library,
                tests=tests,
                py_version=py_ver,
                main_module=main_module,
                strip_libpar=strip_libpar,
                tags=tags,
                lib_dir=lib_dir,
                par_style=par_style,
                emails=emails,
                needed_coverage=needed_coverage,
                argcomplete=argcomplete,
                strict_tabs=strict_tabs,
                compile=compile,
                args=args,
                env=env,
                python=python,
                allocator=allocator,
                check_types=check_types,
            )
            rules.extend(one_set_rules)
            rule_names.append(':' + py_name)

        # If we only have one then we don't need the genrule
        genrule = len(rule_names) > 1

        # Create a genrule to wrap all the tests for easy running
        if genrule and self.get_fbconfig_rule_type() == 'python_unittest':
            attrs = collections.OrderedDict()
            attrs['name'] = name
            attrs['out'] = os.curdir
            attrs['tests'] = rule_names
            # With this we are telling buck we depend on the test targets
            cmds = []
            for test in rule_names:
                cmds.append('echo $(location {})'.format(test))
            attrs['cmd'] = ' && '.join(cmds)
            rules.append(Rule('genrule', attrs))

        return rules

    def create_typecheck(
        self,
        name,
        main_module,
        platform,
        python_platform,
        library,
        deps,
        platform_deps,
        preload_deps,
    ):
        typecheck_deps = deps[:]
        if ':python_typecheck-library' not in typecheck_deps:
            # Buck doesn't like duplicate dependencies.
            typecheck_deps.append('//libfb/py:python_typecheck-library')

        attrs = collections.OrderedDict((
            ('name', name + '-typecheck'),
            ('main_module', 'python_typecheck'),
            ('cxx_platform', platform),
            ('platform', python_platform),
            ('deps', typecheck_deps),
            ('platform_deps', platform_deps),
            ('preload_deps', preload_deps),
            ('package_style', 'inplace'),
            # TODO(ambv): labels here shouldn't be hard-coded.
            ('labels', ['buck', 'python']),
            ('version_universe',
             self.get_version_universe(self.get_py3_version())),
        ))

        if ':' + library.attributes['name'] not in typecheck_deps:
            # If the passed library is not a dependency, add its sources here.
            # This enables python_unittest targets to be type-checked, too.
            for param in ('versioned_srcs', 'srcs', 'resources', 'base_module'):
                val = library.attributes.get(param)
                if val is not None:
                    attrs[param] = val

        if main_module != '__fb_test_main__':
            # Tests are properly enumerated from passed sources (see above).
            # For binary targets, we need this subtle hack to let
            # python_typecheck know where to start type checking the program.
            attrs['env'] = {"PYTHON_TYPECHECK_ENTRY_POINT": main_module}

        return Rule('python_test', attrs)

    def gen_test_modules(self, base_path, library):
        lines = ['TEST_MODULES = [']
        for src in sorted(library.attributes.get('srcs') or ()):
            lines.append(
                '    "{}",'.format(
                    self.file_to_python_module(
                        src,
                        library.attributes.get('base_module') or base_path,
                    )
                )
            )
        lines.append(']')

        name = library.attributes['name']
        gen_attrs = collections.OrderedDict()
        gen_attrs['name'] = name + '-testmodules'
        gen_attrs['out'] = name + '-__test_modules__.py'
        gen_attrs['cmd'] = ' && '.join(
            'echo {} >> $OUT'.format(pipes.quote(line))
            for line in lines
        )
        yield Rule('genrule', gen_attrs)

        lib_attrs = collections.OrderedDict()
        lib_attrs['name'] = name + '-testmodules-lib'
        lib_attrs['base_module'] = ''
        lib_attrs['deps'] = ['//python:testmain', ':' + name]
        lib_attrs['srcs'] = {'__test_modules__.py': ':' + gen_attrs['name']}
        yield Rule('python_library', lib_attrs)

    def file_to_python_module(self, src, base_module):
        """Python implementation of Buck's toModuleName().

        Original in com.facebook.buck.python.PythonUtil.toModuleName.
        """
        src = os.path.join(base_module, src)
        src, ext = os.path.splitext(src)
        return src.replace('/', '.')  # sic, not os.sep
