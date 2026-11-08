
load(
    "@rules_cc//cc:defs.bzl",
    "objc_library",
)

objc_library(
    name = "LegacyDataImportImpl",
    enable_modules = True,
    module_name = "LegacyDataImportImpl",
    srcs = glob([
        "Sources/**/*.m",
        "Sources/**/*.h",
    ]),
    hdrs = glob([
        "PublicHeaders/**/*.h",
    ]),
    includes = [
        "PublicHeaders",
    ],
    sdk_frameworks = [
        "Foundation",
    ],
    visibility = [
        "//visibility:public",
    ],
)
