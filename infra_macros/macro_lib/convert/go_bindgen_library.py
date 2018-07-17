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

with allow_unsafe_import():
    import os.path

macro_root = read_config('fbcode', 'macro_lib', '//macro_lib')
include_defs("{}/convert/base.py".format(macro_root), "base")
include_defs("{}/convert/go.py".format(macro_root), "go")
include_defs("{}/rule.py".format(macro_root))
load("@fbcode_macros//build_defs:platform.bzl", platform_utils="platform")


class GoBindgenLibraryConverter(go.GoConverter):
    def __init__(self, context):
        super(GoBindgenLibraryConverter, self).\
            __init__(context, 'cgo_library')

    def get_fbconfig_rule_type(self):
        return 'go_bindgen_library'

    def get_buck_rule_type(self):
        return 'cgo_library'

    def get_allowed_args(self):
        return set({
            'name',
            'package_name',
            'srcs',
            'headers',
            'deps',
            'preprocessor_flags',
            'platform_preprocessor_flags',
            'cgo_compiler_flags',
            'compiler_flags',
            'platform_compiler_flags',
            'linker_extra_outputs',
            'linker_flags',
            'platform_linker_flags',
            'link_style',
            'raw_headers',
            'visibility',

            'manifest',
            'header_includes',
        })

    def gen_file_copy_rule(self, src):
        filename = os.path.basename(src)

        attrs = {
            'name': "src-copy={}".format(filename),
            'cmd': "cp -r \$(buck root)/{} $OUT".format(src),
            'out': filename
        }
        return 'genrule', attrs

    def gen_bindgen_exe_rule(self, base_path, name, headers, manifest_path):
        cmd = [
            "$(exe //third-party-source/go/github.com/xlab/c-for-go:c-for-go)",
            "-out $OUT", "-ccincl", manifest_path
        ]

        attrs = {
            'name': name,
            'srcs': headers + [manifest_path],
            'cmd': " ".join(cmd),
            'out': 'go-bindgen'
        }
        return 'cxx_genrule', attrs

    def gen_header_rules(self, header_includes):
        rules = []
        parsed_headers = []

        for header in header_includes:
            if header.startswith("//"):  # we need to copy the file
                header = header.replace("//", "")

                rule, attrs = self.gen_file_copy_rule(header)
                rules.append(Rule(rule, attrs))

                parsed_headers.append(":" + attrs['name'])
            else:
                parsed_headers.append(header)
        return parsed_headers, rules

    def fix_relative_paths(self, src, base_path):
        if src.startswith(":"):
            loc = "$(location {})".format(src)
            src = src.split("=")[1]
        else:
            loc = base_path + "/" + src

        return " && sed -i 's@{}@'\"{}\"'@' $OUT".format(src, loc)

    def generate_bindgen_rule(
        self,
        base_path,
        name,
        package_name,
        header_includes,
        manifest
    ):
        # parse headers (copy files prefixed with // or use the given path)
        parsed_headers, rules = self.gen_header_rules(header_includes)

        # run c-for-go bindgen generator
        bindgen_exe = "{}-go-bindgen".format(name)
        rule, attrs = self.gen_bindgen_exe_rule(
            base_path,
            bindgen_exe,
            parsed_headers,
            manifest
        )
        rules.append(Rule(rule, attrs))

        # bindgen generated files include header files with local paths
        # this is replacing the include paths
        fix_headers = ""
        for header_target in parsed_headers:
            fix_headers += self.fix_relative_paths(header_target, base_path)

        # create copy files generated by c-for-go bindgen
        cgo_headers = parsed_headers
        cgo_srcs = []
        expected_go_files = [
            package_name + ".go",
            "cgo_helpers.go",
            "types.go",
            "cgo_helpers.h"
        ]
        buck_platform = platform_utils.get_buck_platform_for_base_path(base_path)
        for filename in expected_go_files:
            rule_name = "{}={}".format(bindgen_exe, filename)

            attrs = {
                'name': rule_name,
                'cmd': "cp $(location :{})/{}/{} $OUT".format(
                    bindgen_exe,
                    package_name,
                    filename,
                ),
                'out': filename,
            }

            attrs['cmd'] += fix_headers

            rule_name = ":{}#{}".format(rule_name, buck_platform)
            if filename.endswith(".go"):
                cgo_srcs.append(rule_name)

                # fix relative paths
                attrs['cmd'] += \
                  self.fix_relative_paths(
                    ":{}-go-bindgen=cgo_helpers.h".format(name),
                    base_path
                )

            elif filename.endswith(".h"):
                cgo_headers.append(rule_name)
            rules.append(Rule('cxx_genrule', attrs))

        return cgo_srcs, cgo_headers, rules

    def convert(
            self,
            base_path,
            name,
            package_name,
            header_includes,
            manifest,
            srcs=None,
            headers=None,
            **kwargs):
        srcs = srcs or []
        headers = headers or []

        extra_srcs, extra_headers, extra_rules = self.generate_bindgen_rule(
            base_path,
            name,
            package_name,
            header_includes,
            manifest)

        return super(GoBindgenLibraryConverter, self).convert(
            base_path,
            name,
            package_name=package_name,
            srcs=srcs + extra_srcs,
            headers=headers + extra_headers,
            **kwargs) + extra_rules
