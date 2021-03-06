# Copyright 2016-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.

from __future__ import absolute_import, division, print_function, unicode_literals

import textwrap

import tests.utils


class ModulesTest(tests.utils.TestCase):

    includes = [("@fbcode_macros//build_defs:modules.bzl", "modules")]

    expected_cmd = (
        r"""set -euo pipefail\n"""
        r"""while test ! -r .projectid -a `pwd` != / ; do cd ..; done\n"""
        r"""args=()\n"""
        r"""args+=($(cxx))\n"""
        r"""args+=($(cxxppflags :foo-helper))\n"""
        r"""args+=($(cxxflags))\n"""
        r"""args+=(\'-fmodules\' \'-Rmodule-build\' \'-fimplicit-module-maps\' \'-fno-builtin-module-map\' \'-fno-implicit-modules\' \'-fmodules-cache-path=/DOES/NOT/EXIST\' \'-Xclang\' \'-fno-modules-global-index\' \'-Wnon-modular-include-in-module\' \'-Xclang\' \'-fno-absolute-module-directory\')\n"""
        r"""args+=(\"-Xclang\" \"-emit-module\")\n"""
        r"""args+=(\"-fmodule-name=\"\'bar\')\n"""
        r"""args+=(\"-x\" \"c++-header\")\n"""
        r"args+=(\"-Xclang\" \"-fno-validate-pch\")\n"
        r"""args+=(\"-I$SRCDIR/module_headers\")\n"""
        r"""args+=(\"$SRCDIR/module_headers/module.modulemap\")\n"""
        r"""args+=(\"-o\" \"-\")\n"""
        r"""for i in \"${!args[@]}\"; do\n"""
        r"""  args[$i]=${args[$i]//$PWD\\//}\n"""
        r"""done\n"""
        r"""function compile() {\n"""
        r"""  echo \"\\$(ls -i \"$SRCDIR/module_headers/module.modulemap\" | awk \'{ print $1 }\')\" > \"$TMP/$1.inode\"\n"""
        r"""  (\"${args[@]}\" 3>&1 1>&2 2>&3 3>&-) 2>\"$TMP/$1\".tmp \\\n"""
        r"""    | >&2 sed \"s|${SRCDIR//$PWD\\//}/module_headers/|third-party-buck/something/|g\"\n"""
        r"""  mv -nT \"$TMP/$1\".tmp \"$TMP/$1\"\n"""
        r"""}\n"""
        r"""! { compile prev2.pcm; compile prev1.pcm; } 2>/dev/null\n"""
        r"""compile module.pcm\n"""
        r"""if ! cmp -s \"$TMP/prev2.pcm\" \"$TMP/prev1.pcm\" || \\\n"""
        r"""   ! cmp -s \"$TMP/prev2.pcm.inode\" \"$TMP/prev1.pcm.inode\" || \\\n"""
        r"""   ! cmp -s \"$TMP/prev1.pcm\" \"$TMP/module.pcm\" || \\\n"""
        r"""   ! cmp -s \"$TMP/prev1.pcm.inode\" \"$TMP/module.pcm.inode\"; then\n"""
        r"""  >&2 echo \"Detected non-determinism building module bar.  Retrying...\"\n"""
        r"""  while ! cmp -s \"$TMP/prev2.pcm\" \"$TMP/prev1.pcm\" || \\\n"""
        r"""        ! cmp -s \"$TMP/prev2.pcm.inode\" \"$TMP/prev1.pcm.inode\" || \\\n"""
        r"""        ! cmp -s \"$TMP/prev1.pcm\" \"$TMP/module.pcm\" || \\\n"""
        r"""        ! cmp -s \"$TMP/prev1.pcm.inode\" \"$TMP/module.pcm.inode\"; do\n"""
        r"""    mv -fT \"$TMP/prev2.pcm\" \"$TMP/bad.pcm\"\n"""
        r"""    mv -fT \"$TMP/prev2.pcm.inode\" \"$TMP/bad.pcm.inode\"\n"""
        r"""    mv -fT \"$TMP/prev1.pcm\" \"$TMP/prev2.pcm\"\n"""
        r"""    mv -fT \"$TMP/prev1.pcm.inode\" \"$TMP/prev2.pcm.inode\"\n"""
        r"""    mv -fT \"$TMP/module.pcm\" \"$TMP/prev1.pcm\"\n"""
        r"""    mv -fT \"$TMP/module.pcm.inode\" \"$TMP/prev1.pcm.inode\"\n"""
        r"""    compile module.pcm 2>/dev/null\n"""
        r"""  done\n"""
        r"""  ! {\n"""
        r"""    archive=\"$TMP/archive.tgz\"\n"""
        r"""    tar -czf \"$archive\" -C \"$TMP\" module.pcm bad.pcm\n"""
        r"""    handle=\"\\$(clowder put -t FBCODE_BUCK_DEBUG_ARTIFACTS \"$archive\")\"\n"""
        r"""    scribe_cat \\\n"""
        r"""      perfpipe_fbcode_buck_clang_module_errors \\\n"""
        r"""      \"{\\\"int\\\": \\\n"""
        r"""          {\\\"time\\\": \\$(date +\"%s\")}, \\\n"""
        r"""        \\\"normal\\\": \\\n"""
        r"""          {\\\"everstore_handle\\\": \\\"$handle\\\", \\\n"""
        r"""           \\\"build_target\\\": \\\"//third-party-buck/something:foo\\\", \\\n"""
        r"""           \\\"build_uuid\\\": \\\"$BUCK_BUILD_ID\\\", \\\n"""
        r"""           \\\"gvfs_version\\\": \\\"\\$(cd / && getfattr -L --only-values -n user.gvfs.version mnt/gvfs)\\\", \\\n"""
        r"""           \\\"sandcastle_alias\\\": \\\"${SANDCASTLE_ALIAS:-}\\\", \\\n"""
        r"""           \\\"sanscastle_job_info\\\": \\\"${SANDCASTLE_NONCE:-}/${SANDCASTLE_INSTANCE_ID:-}\\\"}}\";\n"""
        r"""  }\n"""
        r"""fi\n"""
        r'''mv -nT \"$TMP/module.pcm\" \"$OUT\"'''
    )

    @tests.utils.with_project()
    def test_get_module_name(self, root):
        self.assertSuccess(
            root.runUnitTests(
                self.includes,
                ['modules.get_module_name("fbcode", "base/path", "short-name")'],
            ),
            "fbcode//base/path:short-name",
        )

    @tests.utils.with_project()
    def test_get_module_map(self, root):
        self.assertSuccess(
            root.runUnitTests(
                self.includes,
                [
                    'modules.get_module_map("name", {"header1.h": ["private"], "header2.h": {}})'
                ],
            ),
            textwrap.dedent(
                """\
                module "name" {
                  module "header1.h" {
                    private header "header1.h"
                    export *
                  }
                  module "header2.h" {
                    header "header2.h"
                    export *
                  }
                }
                """
            ),
        )

    @tests.utils.with_project(use_python=False, use_skylark=True)
    def test_gen_tp2_cpp_module_parses_skylark(self, root):
        root.addFile(
            "third-party-buck/something/BUCK",
            textwrap.dedent(
                """
            load("@fbcode_macros//build_defs:modules.bzl", "modules")
            modules.gen_tp2_cpp_module(
                name = "foo",
                module_name = "bar",
                headers = {"module.modulemap": "module.modulemap", "foo.h": "foo.cpp"},
                flags = ["-DFOO"],
                dependencies = [],
                platform = None,
                visibility = None,
            )
            """
            ),
        )
        expected = tests.utils.dedent(
            r"""
cxx_genrule(
  name = "foo",
  cmd = "{cmd}",
  labels = [
    "is_fully_translated",
  ],
  out = "module.pcm",
  srcs = {{
    "module_headers/module.modulemap": "module.modulemap",
    "module_headers/foo.h": "foo.cpp",
  }},
)

cxx_library(
  name = "foo-helper",
  exported_preprocessor_flags = [
    "-DFOO",
  ],
  labels = [
    "generated",
    "is_fully_translated",
  ],
  visibility = [
    "//third-party-buck/something:foo",
  ],
)
        """
        ).format(cmd=self.expected_cmd)
        result = root.runAudit(["third-party-buck/something/BUCK"])
        self.validateAudit({"third-party-buck/something/BUCK": expected}, result)

    @tests.utils.with_project(use_python=True, use_skylark=False)
    def test_gen_tp2_cpp_module_parses_py(self, root):
        root.addFile(
            "third-party-buck/something/BUCK",
            textwrap.dedent(
                """
            load("@fbcode_macros//build_defs:modules.bzl", "modules")
            modules.gen_tp2_cpp_module(
                name = "foo",
                module_name = "bar",
                headers = {"module.modulemap": "module.modulemap", "foo.h": "foo.cpp"},
                flags = ["-DFOO"],
                dependencies = [],
                platform = None,
                visibility = None,
            )
            """
            ),
        )

        expected = tests.utils.dedent(
            r"""
cxx_genrule(
  name = "foo",
  cmd = "{cmd}",
  labels = [
    "is_fully_translated",
  ],
  out = "module.pcm",
  srcs = {{
    "module_headers/foo.h": "foo.cpp",
    "module_headers/module.modulemap": "module.modulemap",
  }},
)

cxx_library(
  name = "foo-helper",
  exported_preprocessor_flags = [
    "-DFOO",
  ],
  labels = [
    "generated",
    "is_fully_translated",
  ],
  visibility = [
    "//third-party-buck/something:foo",
  ],
)
        """
        ).format(cmd=self.expected_cmd)
        result = root.runAudit(["third-party-buck/something/BUCK"])
        self.validateAudit({"third-party-buck/something/BUCK": expected}, result)
