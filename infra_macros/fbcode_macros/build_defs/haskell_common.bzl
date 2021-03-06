# Copyright 2016-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.

load("@fbsource//tools/build_defs:fb_native_wrapper.bzl", "fb_native")
load("@bazel_skylib//lib:paths.bzl", "paths")
load("@fbcode_macros//build_defs/config:read_configs.bzl", "read_boolean", "read_list")
load("@fbcode_macros//build_defs:custom_rule.bzl", "get_project_root_from_gen_dir")
load("@fbcode_macros//build_defs:target_utils.bzl", "target_utils")

_GENERATED_LIB_SUFFIX = "__generated-lib__"

def _read_hs_debug():
    return read_boolean("fbcode", "hs_debug", False)

def _read_hs_eventlog():
    return read_boolean("fbcode", "hs_eventlog", False)

def _read_hs_profile():
    return read_boolean("fbcode", "hs_profile", False)

def _read_extra_ghc_compiler_flags():
    return read_list("haskell", "extra_compiler_flags", [], " ")

def _read_extra_ghc_linker_flags():
    return read_list("haskell", "extra_linker_flags", [], " ")

def _dll_needed_syms_list_rule(name, dlls, visibility):
    """
    Creates the genrule to pull symbols from a list of dll dependencies

    Args:
        name: The name of the original rule
        dlls: A list of string labels that point to dll to extract symbols from
        visibility: The visibility of the genrule

    Returns:
        A package relative target with the name of the generated rule
    """
    cmd = "nm -gPu {} | awk '{{print $1}}' | grep -v '^BuildInfo_k' | sort > $OUT".format(
        " ".join(["$(location " + dll + ")" for dll in dlls]),
    )
    rule_name = name + "-syms"
    fb_native.cxx_genrule(
        name = rule_name,
        out = "symbols.txt",
        visibility = visibility,
        cmd = cmd,
    )
    return ":" + rule_name

def _dll_syms_linker_script_rule(name, symbols_rule, visibility):
    """
    Creates the genrule to take extracted symbols, and turn it into a linker script

    Args:
        name: The name of the original rule
        symbols_rule: The target of the symbols rule from _dll_needed_syms_list_rule
        visibility: The visibility of the genrule

    Returns:
        A package relative target with the name of the generated rule
    """
    rule_name = name + "-syms-linker-script"
    fb_native.cxx_genrule(
        name = rule_name,
        visibility = visibility,
        out = "extern_symbols.txt",
        cmd = '''awk '{{print "EXTERN("$1")"}}' $(location {}) > "$OUT"'''.format(symbols_rule),
    )
    return ":" + rule_name

def _dll_rules(
        name,
        lib_name,
        dll_root,
        rule_type_filter,
        rule_name_filter,
        dll_type,
        fbcode_dir,
        visibility):
    """
    Create a rule to link a DLL

    Args:
        name: The name of the rule
        lib_name: The name of the output artifact
        rule_type_filter: A regular expression for filtering types of rules that ldflags come from (or None)
        rule_name_filter: A regular expression for filtering names of rules that ldflags come from (or None)
        dll_type: The type of library that is being referenced.
                  One of 'static', 'static-pic', or 'shared'
        visibility: The visibility for the rule
    """

    cmd = ["$(ld)"]

    # Build a shared library.
    if dll_type == "static" or dll_type == "static-pic":
        cmd.append("-r")
    elif dll_type == "shared":
        cmd.append("-shared")
    else:
        fail("dll_type must be one of static, static-pic or shared")
    cmd.append("-nostdlib")
    cmd.extend(["-o", "$OUT"])

    # When GHC links DSOs, it sets this flag to prevent non-PIC symbols
    # from leaking to the dynamic symbol table, as it breaks linking.
    cmd.append("-Wl,-Bsymbolic")

    # Add-in the macro to add the transitive deps to the link line.  For
    # shared link styles, we do a shared link, but for anything else (i.e.
    # static, static-pic), we always do a `static` link, as we need to
    # support profiled builds with DSOs and this requires that we issue
    # an `-hisuf p_hi`, which we can't supply in addition to the 'dyn_hi'
    # suffix that a `-dynamic -fPIC` (i.e. static-pic) compilation rquires.
    # This is fine for GHC-built DSOs. To quote https://fburl.com/ze4ni010:
    # "Profiled code isn't yet really position-independent even when -fPIC
    # is specified. Building profiled dynamic libraries therefore fails on
    # Mac OS X (Linux silently accepts relocations - it's just slightly bad
    # for performance)." We also set a "filter" here so we only get haskell
    # rules in the link.
    if dll_type == "shared" or dll_type == "static-pic":
        dll_type_filter = "ldflags-static-pic-filter"
    else:  # 'static'
        dll_type_filter = "ldflags-static-filter"
    cmd.append(
        "$({} ^{}[(]{}[)]$ {})"
            .format(
            dll_type_filter,
            rule_type_filter or ".*",
            rule_name_filter or ".*",
            dll_root,
        ),
    )

    # TODO: Linker script must be run from repo root for now. This should probably changed
    cmd = "cd {} && {}".format(paths.join("$GEN_DIR", fbcode_dir), " ".join(cmd))

    fb_native.cxx_genrule(
        name = name,
        visibility = visibility,
        out = lib_name,
        cmd = cmd,
    )

# TODO: Remove the default value when haskell rules get converted
def _convert_dlls(
        name,
        platform,
        buck_platform,
        dlls,
        visibility):
    """
    Creates a genrule that generates DLLs for haskell

    Args:
        name: The name of the genrule, and base name for intermediate rules
        platform: The fbcode platform to use for generating the deps query
        buck_platform: The buck platform to use for generating the deps query
        dlls: A dictionary of:
            <desired .so name, including syntax> ->
                (dll_root, type_filter, name_filter, dll_type) where dll_root is the
                target that provides the library, type_filter is a regex to filter
                what type of rules to grab symbols from, name_filter is a regex for
                names to grab symbols from, and dll_type is one of "shared", "static",
                or "static-pic"
        visibility: The visibility for the rule

    Returns:
        A tuple of (
            [dependencies to use generated dlls],
            [ldflags to use when linking with the dlls],
            [queries that can be used with deps_query attributes],
        )
    """

    base_path = native.package_name()
    if not dlls:
        fail("No dlls were provided")

    deps = []
    ldflags = []
    dep_queries = []

    # Generate the rules that link the DLLs.
    dll_targets = {}
    for dll_lib_name, (dll_root, type_filter, name_filter, dll_type) in dlls.items():
        dll_name = name + "." + dll_lib_name
        dll_targets[dll_lib_name] = ":" + dll_name
        _dll_rules(
            dll_name,
            dll_lib_name,
            dll_root + "-dll-root",
            type_filter,
            name_filter,
            dll_type,
            get_project_root_from_gen_dir(),
            visibility,
        )

    # Create the rule which extracts the symbols from all DLLs.
    sym_target = _dll_needed_syms_list_rule(name, dll_targets.values(), visibility)

    # Create the rule which sets up a linker script with all missing DLL
    # symbols marked as extern.
    syms_linker_script_target = _dll_syms_linker_script_rule(name, sym_target, visibility)
    ldflags.append("$(location {})".format(syms_linker_script_target))

    # Make sure all symbols needed by the DLLs are exported to the binary's
    # dynamic symbol table.
    ldflags.append("-Xlinker")
    ldflags.append("--export-dynamic")

    # Form a sub-query which matches all deps relevant to the current
    # platform.
    first_order_dep_res = [
        # Match any non-third-party deps.
        "((?!//third-party-buck/.{0,100}).*)",
        # Match any third-party deps for the current platform.
        "(//third-party-buck/{0}/.*)".format(platform),
    ]

    # Form a sub-query to exclude all of the generated-lib deps, in particular
    # sanitizer-configuration libraries
    generated_lib = "(?<!{})".format(_GENERATED_LIB_SUFFIX)
    first_order_deps = (
        'filter("^({prefix}){exclude_generated}$", first_order_deps())'.format(
            prefix = "|".join(first_order_dep_res),
            exclude_generated = generated_lib,
        )
    )

    # Form a query which resolve to all the first-order deps of all DLLs.
    # These form roots which need to be linked into the top-level binary.
    dll_deps = []
    for dll_lib_name, (_, type_filter, name_filter, _) in dlls.items():
        dll_nodes = (
            # The `deps` function's second argument is the depth of the
            # search and while we don't actually want to override its
            # default value, we need to set it in order to use the third
            # argument, so just set it to some arbitrarily high value.
            ("deps({root}, 4000," +
             ' kind("^{type_filter}$",' +
             '  filter("^{name_filter}$",' +
             "   {deps})))")
                .format(
                root = "//{}:{}.{}".format(base_path, name, dll_lib_name),
                type_filter = type_filter or ".*",
                name_filter = name_filter or ".*",
                deps = first_order_deps,
            )
        )

        # We need to link deep on Haskell libraries because of cross-module
        # optimizations like inlining.
        # Eg. we import A, inline something from A that refers to B and now
        # have a direct symbol reference to B.
        dll_deps.append(
            ("deps(" +
             ' deps({nodes}, 4000, kind("haskell_library", {first_order_deps})),' +
             " 1," +
             ' kind("library", {first_order_deps}))' +
             "- {nodes}")
                .format(nodes = dll_nodes, first_order_deps = first_order_deps),
        )
    dep_query = " union ".join(["({})".format(d) for d in dll_deps])
    dep_queries.append(dep_query)
    # This code is currently only used for Haskell code in Sigma
    # Search for Note [Sigma hot-swapping code]

    cmds = ["mkdir -p $OUT"]
    for dll_name, dll_target in dll_targets.items():
        cmds.append('cp $(location {}) "$OUT"/{}'.format(dll_target, dll_name))
    cmd = " && ".join(cmds)

    # Create the rule which copies the DLLs into the output location.
    rule_name = name + ".dlls"
    fb_native.cxx_genrule(
        name = rule_name,
        visibility = visibility,
        out = "out",
        cmd = cmd,
    )
    deps.append(
        target_utils.RootRuleTarget(base_path, "{}#{}".format(rule_name, buck_platform)),
    )
    return deps, ldflags, dep_queries

haskell_common = struct(
    convert_dlls = _convert_dlls,
    read_extra_ghc_compiler_flags = _read_extra_ghc_compiler_flags,
    read_extra_ghc_linker_flags = _read_extra_ghc_linker_flags,
    read_hs_debug = _read_hs_debug,
    read_hs_eventlog = _read_hs_eventlog,
    read_hs_profile = _read_hs_profile,
)
